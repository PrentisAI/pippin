#!/usr/bin/env python3
"""Q4 (optional): association of daily Citi Bike trips with NYC daily weather (Open-Meteo archive)."""
import json, os, sys, time, datetime as dt, urllib.request
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.nonparametric.smoothers_lowess import lowess
from scipy.stats import spearmanr
from pandas.tseries.holiday import USFederalHolidayCalendar

OUT = "/app/output"; TAB = f"{OUT}/tables"; FIG = f"{OUT}/figures"
os.makedirs(TAB, exist_ok=True); os.makedirs(FIG, exist_ok=True)
URL = ("https://archive-api.open-meteo.com/v1/archive?latitude=40.7829&longitude=-73.9654"
       "&start_date=2023-01-01&end_date=2024-12-31"
       "&daily=temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum,rain_sum,snowfall_sum"
       "&timezone=America%2FNew_York")
rng = np.random.default_rng(20240607)

# ---------------- fetch ----------------
raw = None; err = None
for attempt in range(5):
    try:
        with urllib.request.urlopen(URL, timeout=60) as r:
            raw = json.loads(r.read().decode()); break
    except Exception as e:
        err = repr(e); time.sleep(3 * (attempt + 1))
if raw is None or "daily" not in raw:
    with open(f"{TAB}/weather_SKIPPED.md", "w") as f:
        f.write(f"# Weather analysis skipped\n\nOpen-Meteo fetch failed after 5 attempts: {err}\n\nNo weather values were invented.\n")
    print("SKIPPED", err); sys.exit(0)

w = pd.DataFrame(raw["daily"]).rename(columns={"time": "date"})
w.to_csv(f"{TAB}/weather_daily.csv", index=False)
with open(f"{TAB}/weather_source.json", "w") as f:
    json.dump({"source_url": URL, "retrieved_on": dt.date.today().isoformat(),
               "units": raw.get("daily_units"), "location": "NYC Central Park (40.7829, -73.9654); Open-Meteo reanalysis grid cell, not a station reading",
               "timezone": raw.get("timezone"), "elevation_m": raw.get("elevation")}, f, indent=2)
print(w.describe().T)
assert len(w) == 731, len(w)
assert w[["temperature_2m_mean", "precipitation_sum"]].notna().all().all(), "missing weather values"

# ---------------- join ----------------
dc = pd.read_csv(f"{TAB}/daily_counts.csv", parse_dates=["date"])
p = dc.pivot_table(index="date", columns="member_casual", values="trips", aggfunc="sum")
p["total"] = p.sum(axis=1)
p = p.reset_index()
w["date"] = pd.to_datetime(w["date"])
d = p.merge(w, on="date", how="inner", validate="1:1")
assert len(d) == 731
d["year"] = d.date.dt.year; d["month"] = d.date.dt.month; d["dow"] = d.date.dt.dayofweek
d["weekend"] = d.dow >= 5
hol = USFederalHolidayCalendar().holidays("2023-01-01", "2024-12-31")
d["holiday"] = d.date.isin(hol).astype(int)
d["temp"] = d.temperature_2m_mean; d["precip"] = d.precipitation_sum
d["pbin"] = pd.cut(d.precip, [-1, 0, 2.5, 10, 1e9], labels=["0", "0-2.5", "2.5-10", ">10"], right=True)
# right=True: (-1,0] = 0 ; (0,2.5]; (2.5,10]; >10
d["snow_day"] = (d.snowfall_sum > 0).astype(int)
for c in ["total", "member", "casual"]:
    d[f"log_{c}"] = np.log(d[c])
d.to_csv(f"{TAB}/weather_joined_daily.csv", index=False)
print(d.pbin.value_counts().sort_index())

num = {"n_days": int(len(d)), "temp_range_C": [float(d.temp.min()), float(d.temp.max())],
       "precip_days_gt0": int((d.precip > 0).sum()), "pbin_counts": {str(k): int(v) for k, v in d.pbin.value_counts().sort_index().items()},
       "units": {"temperature": "deg C", "precipitation": "mm", "snowfall": "cm (Open-Meteo)"},
       "holidays_in_sample": int(d.holiday.sum())}

# ---------------- Spearman with moving-block bootstrap CI ----------------
def block_boot_spearman(x, y, B=2000, block=14):
    x = np.asarray(x); y = np.asarray(y); n = len(x)
    k = int(np.ceil(n / block)); rs = np.empty(B)
    for b in range(B):
        st = rng.integers(0, n - block + 1, k)
        idx = np.concatenate([np.arange(s, s + block) for s in st])[:n]
        rs[b] = spearmanr(x[idx], y[idx])[0]
    return np.percentile(rs, [2.5, 97.5])

rows = []
subsets = {"all days": d, "weekdays": d[~d.weekend], "weekends": d[d.weekend]}
for sname, s in subsets.items():
    s = s.sort_values("date")
    for out in ["total", "member", "casual"]:
        for wx, lab in [("temp", "mean temp"), ("precip", "precipitation")]:
            rho, pv = spearmanr(s[wx], s[out])
            # block bootstrap on the (gappy for subsets) ordered series: blocks of 10 obs for subsets keep local dependence approx
            blk = 14 if sname == "all days" else 10
            lo, hi = block_boot_spearman(s[wx].values, s[out].values, block=blk)
            rows.append(dict(subset=sname, outcome=out, weather=lab, n=len(s), spearman_rho=rho, ci_lo=lo, ci_hi=hi, p_naive=pv))
sp = pd.DataFrame(rows)
sp.to_csv(f"{TAB}/weather_spearman.csv", index=False)
print(sp.round(3).to_string())
num["spearman"] = sp.round(4).to_dict(orient="records")

# also spearman controlling season crudely: within-month-demeaned ranks
def within_month_rho(df, xcol, ycol):
    xr = df.groupby("month")[xcol].transform(lambda v: v - v.mean())
    yr = df.groupby("month")[ycol].transform(lambda v: np.log(v) - np.log(v).mean())
    return spearmanr(xr, yr)[0]
num["within_month_spearman_temp_total"] = float(within_month_rho(d, "temp", "total"))

# ---------------- regressions ----------------
dow_f = "C(dow)"
base_cal = f"{dow_f} + holiday + C(year)"
specs = {
    "M0_unadjusted_linear_temp": "{y} ~ temp",
    "M1_calendar_only": f"{{y}} ~ {base_cal}",
    "M2_calendar_linear_temp": f"{{y}} ~ temp + {base_cal}",
    "M3_calendar_quad_temp_precip": f"{{y}} ~ temp + I(temp**2) + C(pbin, Treatment('0')) + {base_cal}",
    "M4_month_FE_linear_temp_precip": f"{{y}} ~ temp + C(pbin, Treatment('0')) + C(month) + {base_cal}",
}
res_rows = []; fits = {}
def pct(b, lo, hi): return [100 * (np.exp(v) - 1) for v in (b, lo, hi)]
def add_row(model, y, term, b, se, lo, hi, pv, n, r2, adj):
    pc = pct(b, lo, hi)
    res_rows.append(dict(model=model, outcome=y.replace("log_", ""), term=term, coef_log=b, se_hac=se,
                         pct_effect=pc[0], pct_ci_lo=pc[1], pct_ci_hi=pc[2], p=pv, n=n, r2=r2, adj_r2=adj))

for mname, f in specs.items():
    for y in ["log_total", "log_member", "log_casual"]:
        m = smf.ols(f.format(y=y), data=d).fit(cov_type="HAC", cov_kwds={"maxlags": 7})
        fits[(mname, y)] = m
        ci = m.conf_int()
        keep = [t for t in m.params.index if t.startswith("temp") or t.startswith("I(temp") or "pbin" in t or t == "holiday"]
        for t in keep:
            add_row(mname, y, t, m.params[t], m.bse[t], ci.loc[t, 0], ci.loc[t, 1], m.pvalues[t], int(m.nobs), m.rsquared, m.rsquared_adj)
        # derived +10 C contrasts for the quadratic model
        if mname == "M3_calendar_quad_temp_precip":
            names = list(m.params.index)
            for T0 in [0, 10, 20]:
                c = np.zeros(len(names)); c[names.index("temp")] = 10; c[names.index("I(temp ** 2)")] = (T0 + 10) ** 2 - T0 ** 2
                tt = m.t_test(c); b = float(tt.effect[0]); se = float(tt.sd[0, 0]); cil = tt.conf_int()[0]
                add_row(mname, y, f"derived: +10C from {T0}C to {T0+10}C", b, se, cil[0], cil[1], float(tt.pvalue), int(m.nobs), m.rsquared, m.rsquared_adj)
        if mname in ("M2_calendar_linear_temp", "M4_month_FE_linear_temp_precip", "M0_unadjusted_linear_temp"):
            pass
mod = pd.DataFrame(res_rows)
# express linear temp coefficient as % per +10 C
lin = mod.term == "temp"
mod["pct_per_10C"] = np.where(lin, 100 * (np.exp(10 * mod.coef_log) - 1), np.nan)
mod["pct_per_10C_ci_lo"] = np.where(lin, 100 * (np.exp(10 * (mod.coef_log - 1.96 * mod.se_hac)) - 1), np.nan)
mod["pct_per_10C_ci_hi"] = np.where(lin, 100 * (np.exp(10 * (mod.coef_log + 1.96 * mod.se_hac)) - 1), np.nan)
mod.to_csv(f"{TAB}/weather_models.csv", index=False)
print(mod[mod.outcome == "total"].round(3).to_string())

# variance-share decomposition (total trips)
def r2(f): return smf.ols(f.format(y="log_total"), data=d).fit().rsquared
cal_month = f"{{y}} ~ C(month) + {base_cal}"
num["r2"] = {"temp_only": r2(specs["M0_unadjusted_linear_temp"]), "calendar_only(dow+holiday+year)": r2(specs["M1_calendar_only"]),
             "month_FE+calendar": r2(cal_month),
             "calendar+quad_temp+precip": r2(specs["M3_calendar_quad_temp_precip"]),
             "month_FE+calendar+temp+precip": r2(specs["M4_month_FE_linear_temp_precip"])}

def g(model, y, term):
    r = mod[(mod.model == model) & (mod.outcome == y) & (mod.term == term)].iloc[0]
    return {k: float(r[k]) for k in ["coef_log", "se_hac", "pct_effect", "pct_ci_lo", "pct_ci_hi", "p", "pct_per_10C", "pct_per_10C_ci_lo", "pct_per_10C_ci_hi"]}
terms_p = [t for t in mod.term.unique() if "pbin" in t]
key = {}
for y in ["total", "member", "casual"]:
    key[y] = {"M0_unadjusted_temp": g("M0_unadjusted_linear_temp", y, "temp"),
              "M2_calendar_linear_temp": g("M2_calendar_linear_temp", y, "temp"),
              "M4_monthFE_temp": g("M4_month_FE_linear_temp_precip", y, "temp"),
              "M3_derived": {t: g("M3_calendar_quad_temp_precip", y, t) for t in mod.term.unique() if t.startswith("derived")},
              "M3_precip": {t: g("M3_calendar_quad_temp_precip", y, t) for t in terms_p},
              "M4_precip": {t: g("M4_month_FE_linear_temp_precip", y, t) for t in terms_p}}
num["key_effects"] = key

# diagnostics: residual autocorrelation of the main model + peak temp of quad fit
m3 = fits[("M3_calendar_quad_temp_precip", "log_total")]
from statsmodels.stats.stattools import durbin_watson
num["durbin_watson_M3_total"] = float(durbin_watson(m3.resid))
b1, b2 = m3.params["temp"], m3.params["I(temp ** 2)"]
num["M3_total_quadratic_vertex_C"] = float(-b1 / (2 * b2)) if b2 < 0 else None
# sensitivity: excluding snow days & using HAC lag 14
m3b = smf.ols(specs["M3_calendar_quad_temp_precip"].format(y="log_total"), data=d).fit(cov_type="HAC", cov_kwds={"maxlags": 14})
num["M3_total_se_hac14_vs_7"] = {"temp_se_7": float(m3.bse["temp"]), "temp_se_14": float(m3b.bse["temp"])}

# ---------------- figures ----------------
COL = {"weekday": "#2a6f97", "weekend": "#d1495b"}
fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
for ax, yr in zip(axes, [2023, 2024]):
    s = d[d.year == yr]
    for lab, mask in [("weekday", ~s.weekend), ("weekend", s.weekend)]:
        ss = s[mask]
        ax.scatter(ss.temp, ss.total / 1e3, s=14, alpha=.6, color=COL[lab], label=f"{lab} (n={len(ss)})", edgecolor="none")
        lw = lowess(ss.total / 1e3, ss.temp, frac=.5)
        ax.plot(lw[:, 0], lw[:, 1], color=COL[lab], lw=2)
    ax.set_title(str(yr)); ax.set_xlabel("Daily mean temperature (°C), Central Park")
    ax.grid(alpha=.25)
    for sp_ in ["top", "right"]: ax.spines[sp_].set_visible(False)
axes[0].set_ylabel("Citi Bike trips per day (thousands)"); axes[0].legend(frameon=False)
r = sp[(sp.subset == "all days") & (sp.outcome == "total") & (sp.weather == "mean temp")].iloc[0]
fig.suptitle(f"Daily trips rise with temperature (association only; Spearman ρ={r.spearman_rho:.2f}, 95% block-bootstrap CI {r.ci_lo:.2f} to {r.ci_hi:.2f}; lines = LOWESS)", fontsize=10)
fig.tight_layout(); fig.savefig(f"{FIG}/fig_temp_vs_trips.png", dpi=160); plt.close(fig)

fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
labels = ["0", "0-2.5", "2.5-10", ">10"]
for ax, (ttl, s) in zip(axes, [("All days", d), ("Weekdays, Apr–Oct only (limits seasonal confounding)", d[(~d.weekend) & d.month.between(4, 10)])]):
    data = [s[s.pbin == l].total / 1e3 for l in labels]
    ax.boxplot(data, tick_labels=[f"{l} mm\n(n={len(x)})" for l, x in zip(labels, data)], showfliers=True,
               medianprops=dict(color="k"), boxprops=dict(color="#2a6f97"), flierprops=dict(marker=".", markersize=4, markerfacecolor="#999", markeredgecolor="none"))
    for i, x in enumerate(data, 1):
        ax.scatter(np.full(len(x), i) + rng.uniform(-.15, .15, len(x)), x, s=6, alpha=.35, color="#2a6f97", edgecolor="none")
    ax.set_title(ttl, fontsize=10); ax.set_xlabel("Daily precipitation"); ax.grid(axis="y", alpha=.25)
    for sp_ in ["top", "right"]: ax.spines[sp_].set_visible(False)
axes[0].set_ylabel("Citi Bike trips per day (thousands)")
fig.suptitle("Daily trips by precipitation bin (association only)", fontsize=10)
fig.tight_layout(); fig.savefig(f"{FIG}/fig_precip_vs_trips.png", dpi=160); plt.close(fig)

# ---------------- provenance & save ----------------
num["source"] = json.load(open(f"{TAB}/weather_source.json"))
num["notes"] = ["Association only; no causal claim.", "Season (daylight, school calendar, tourism) confounds temperature; month-FE model identifies temp from within-month variation.",
                "Open-Meteo values are a gridded reanalysis for one point (Central Park) used as a proxy for city-wide weather.",
                "Daily precipitation total loses within-day timing.", "HAC (Newey-West, 7 lags) SEs; Spearman CIs use moving-block bootstrap (block 14 days all-days, 10 obs for weekday/weekend subsets)."]
with open(f"{TAB}/weather_numbers.json", "w") as f:
    json.dump(num, f, indent=2, default=float)
print(json.dumps({k: num[k] for k in ["r2", "within_month_spearman_temp_total", "durbin_watson_M3_total", "M3_total_quadratic_vertex_C", "M3_total_se_hac14_vs_7"]}, indent=1))
print("DONE")
