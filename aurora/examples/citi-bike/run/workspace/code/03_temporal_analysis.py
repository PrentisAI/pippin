#!/usr/bin/env python3
"""03_temporal_analysis.py - seasonal, day-of-week x hour and member-vs-casual patterns.

Usage: python3 -I /app/code/03_temporal_analysis.py
Inputs : /app/output/tables/{monthly_counts,daily_counts,hourly_dow,duration_by_type}.csv, /app/work/flagged.duckdb
Outputs: /app/output/figures/*.png, /app/output/tables/*.csv, /app/output/tables/findings_numbers.json
Descriptive only: no causal claims. Monthly totals are census counts (no sampling error);
uncertainty = bootstrap over days (or ISO-week blocks) = between-day variability.
"""
import json, time
import numpy as np, pandas as pd, duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from scipy.spatial.distance import jensenshannon
from scipy.signal import find_peaks
from pandas.tseries.holiday import USFederalHolidayCalendar

T0 = time.time()
TAB, FIG = "/app/output/tables", "/app/output/figures"
B = 2000
SEED = 20240501
YEARS = (2023, 2024)
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HRS = np.arange(24)

# ---- palette (reference palette, light mode): member/casual fixed everywhere
C_MEM, C_CAS = "#2a78d6", "#eb6834"
C_WD, C_WE = "#6250d6", "#1baf7a"           # weekday / weekend
C_Y23, C_Y24 = "#8a8a85", "#0b0b0b"         # year (grey dashed / ink solid)
C_WIN, C_SUM = "#6250d6", "#008300"         # Jan-Feb / Jul-Aug
SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({
    "figure.dpi": 100, "savefig.dpi": 170, "figure.facecolor": SURF, "axes.facecolor": SURF,
    "savefig.facecolor": SURF, "axes.edgecolor": INK2, "axes.labelcolor": INK2, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.axisbelow": True,
    "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "legend.frameon": False, "lines.linewidth": 2.0,
})


def log(m):
    print(f"[{time.time() - T0:6.1f}s] {m}", flush=True)


def save(fig, name):
    fig.savefig(f"{FIG}/{name}", dpi=170, bbox_inches="tight")
    plt.close(fig)
    log(f"figure {name}")


def millions(x, _):
    return f"{x / 1e6:.1f}M"


def thousands(x, _):
    return f"{x / 1e3:.0f}k"


# ------------------------------------------------------------------ load
mc = pd.read_csv(f"{TAB}/monthly_counts.csv")
dc = pd.read_csv(f"{TAB}/daily_counts.csv", parse_dates=["date"])
hd = pd.read_csv(f"{TAB}/hourly_dow.csv", parse_dates=["date"])
for d in (dc, hd):
    d["is_weekend"] = d["is_weekend"].astype(str).str.lower().eq("true")

# consistency of the aggregate tables used below
assert dc.groupby(["year", "month", "member_casual"]).trips.sum().reset_index().merge(
    mc, on=["year", "month", "member_casual"]).eval("trips_x == trips_y").all()
assert hd.trips.sum() == mc.trips.sum() == dc.trips.sum()
TOTAL_TRIPS = int(mc.trips.sum())

hol = set(pd.to_datetime(USFederalHolidayCalendar().holidays("2023-01-01", "2024-12-31")))
DST = set(pd.to_datetime(["2023-03-12", "2023-11-05", "2024-03-10", "2024-11-03"]))

# date-level metadata and hourly matrices (dates x 24, zero-filled in source)
meta = dc.groupby("date").agg(year=("year", "first"), month=("month", "first"), dow=("dow", "first"),
                              is_weekend=("is_weekend", "first"), day_total=("trips", "sum")).reset_index()
meta["is_holiday"] = meta.date.isin(hol)
meta["is_dst"] = meta.date.isin(DST)
meta = meta.sort_values("date").reset_index(drop=True)
dates = meta.date.values


def hmat(typ=None):
    d = hd if typ is None else hd[hd.member_casual == typ]
    M = d.groupby(["date", "hour"]).trips.sum().unstack("hour").reindex(meta.date).reindex(columns=HRS)
    assert not M.isna().any().any()
    return M.values.astype(float)


H_ALL, H_MEM, H_CAS = hmat(), hmat("member"), hmat("casual")
assert np.allclose(H_ALL.sum(1), meta.day_total.values)
assert np.allclose(H_ALL, H_MEM + H_CAS)
rng = np.random.default_rng(SEED)
FN = {"meta": {"seed": SEED, "bootstrap_resamples": B, "total_clean_trips": TOTAL_TRIPS,
               "holidays_excluded_in_sensitivity": sorted(str(x.date()) for x in hol),
               "dst_dates_excluded_in_sensitivity": sorted(str(x.date()) for x in DST),
               "hour_windows": {"am_peak_search": "6-10", "pm_peak_search": "15-20", "midday_ref": "11-14 (mean)",
                                "weekend_peak_search": "9-19", "fixed_peak_share_windows": "07-09 and 16-18"}}}


def bidx(n, rg=None):
    return (rg or rng).integers(0, n, size=(B, n))


def boot_profiles(H, idx):
    out = np.empty((idx.shape[0], H.shape[1]))
    for i in range(0, idx.shape[0], 200):
        out[i:i + 200] = H[idx[i:i + 200]].mean(1)
    return out


def ci(a, axis=0):
    return np.percentile(a, 2.5, axis=axis), np.percentile(a, 97.5, axis=axis)


def pk(P):
    """peak metrics for profiles P (...,24)"""
    am = 6 + P[..., 6:11].argmax(-1)
    pm = 15 + P[..., 15:21].argmax(-1)
    mid = P[..., 11:15].mean(-1)
    amv = np.take_along_axis(P, am[..., None], -1)[..., 0]
    pmv = np.take_along_axis(P, pm[..., None], -1)[..., 0]
    wk = 9 + P[..., 9:20].argmax(-1)
    wkv = np.take_along_axis(P, wk[..., None], -1)[..., 0]
    return dict(am=am, pm=pm, mid=mid, amv=amv, pmv=pmv, am_r=amv / mid, pm_r=pmv / mid, wk=wk, wkv=wkv,
                wk_share=wkv / P.sum(-1))


# =================================================================== Q1 seasonality
log("Q1 seasonality")
piv = mc.pivot_table(index=["year", "month"], columns="member_casual", values="trips", aggfunc="sum").reset_index()
piv["trips"] = piv.member + piv.casual
nd = meta.groupby(["year", "month"]).size().rename("days").reset_index()
piv = piv.merge(nd, on=["year", "month"])
piv["trips_per_day"] = piv.trips / piv.days
piv["share_of_year"] = piv.trips / piv.groupby("year").trips.transform("sum")
piv["casual_share"] = piv.casual / piv.trips
rows = []
for (y, m), g in meta.groupby(["year", "month"]):
    dt = g.day_total.values
    bi = bidx(len(dt))
    bm = dt[bi].mean(1)
    lo, hi = ci(bm)
    rows.append(dict(year=y, month=m, daily_mean=dt.mean(), daily_sd=dt.std(ddof=1), daily_ci_lo=lo, daily_ci_hi=hi))
piv = piv.merge(pd.DataFrame(rows), on=["year", "month"])
assert np.allclose(piv.daily_mean, piv.trips_per_day)
for t in ("member", "casual"):                      # per-type trips per day with CI
    r2 = []
    for (y, m), g in meta.groupby(["year", "month"]):
        ix = np.where((meta.year == y) & (meta.month == m))[0]
        v = (H_MEM if t == "member" else H_CAS)[ix].sum(1)
        lo, hi = ci(v[bidx(len(v))].mean(1))
        r2.append(dict(year=y, month=m, **{f"{t}_per_day": v.mean(), f"{t}_per_day_ci_lo": lo, f"{t}_per_day_ci_hi": hi}))
    piv = piv.merge(pd.DataFrame(r2), on=["year", "month"])
# casual share CI (ratio of sums, resample days within month)
cl, ch = [], []
for (y, m) in zip(piv.year, piv.month):
    ix = np.where((meta.year == y) & (meta.month == m))[0]
    c, t = H_CAS[ix].sum(1), H_ALL[ix].sum(1)
    bi = bidx(len(ix))
    s = c[bi].sum(1) / t[bi].sum(1)
    a, b_ = ci(s)
    cl.append(a); ch.append(b_)
piv["casual_share_ci_lo"], piv["casual_share_ci_hi"] = cl, ch
# year-over-year ratio
p23 = piv[piv.year == 2023].set_index("month"); p24 = piv[piv.year == 2024].set_index("month")
yoy = pd.DataFrame({"yoy_trips_ratio_2024_over_2023": p24.trips / p23.trips,
                    "yoy_per_day_ratio_2024_over_2023": p24.trips_per_day / p23.trips_per_day,
                    "yoy_member_per_day_ratio": p24.member_per_day / p23.member_per_day,
                    "yoy_casual_per_day_ratio": p24.casual_per_day / p23.casual_per_day}).reset_index()
piv = piv.merge(yoy, on="month").sort_values(["year", "month"]).reset_index(drop=True)
piv.insert(2, "month_name", piv.month.map(lambda m: MON[m - 1]))
piv.to_csv(f"{TAB}/seasonality_summary.csv", index=False)

# peak / trough per year (+ bootstrap of ratio of daily means, identity re-determined each resample)
pt_rows = []
dm = {y: [meta.day_total.values[(meta.year == y) & (meta.month == m)] for m in range(1, 13)] for y in YEARS}
mem_d = {y: [H_MEM.sum(1)[(meta.year == y) & (meta.month == m)] for m in range(1, 13)] for y in YEARS}
cas_d = {y: [H_CAS.sum(1)[(meta.year == y) & (meta.month == m)] for m in range(1, 13)] for y in YEARS}


def peak_trough(series_by_month, y, label):
    tot = piv[piv.year == y]
    if label == "all":
        tt = tot.trips.values; pdv = tot.trips_per_day.values; lst = dm[y]
    elif label == "member":
        tt = tot.member.values; pdv = tot.member_per_day.values; lst = mem_d[y]
    else:
        tt = tot.casual.values; pdv = tot.casual_per_day.values; lst = cas_d[y]
    out = dict(year=y, series=label, total_trips=int(tt.sum()))
    for nm, v in (("total", tt), ("per_day", pdv)):
        out.update({f"peak_month_{nm}": MON[v.argmax()], f"peak_value_{nm}": v.max(),
                    f"trough_month_{nm}": MON[v.argmin()], f"trough_value_{nm}": v.min(),
                    f"peak_trough_ratio_{nm}": v.max() / v.min()})
    # bootstrap days within each month -> 12 means -> ratio
    bms = np.stack([np.array(x)[bidx(len(x))].mean(1) for x in lst], axis=1)   # (B,12)
    r = bms.max(1) / bms.min(1)
    lo, hi = ci(r)
    out.update(peak_trough_ratio_per_day_ci_lo=lo, peak_trough_ratio_per_day_ci_hi=hi,
               boot_prob_same_peak_month=float((bms.argmax(1) == pdv.argmax()).mean()),
               boot_prob_same_trough_month=float((bms.argmin(1) == pdv.argmin()).mean()))
    return out


for y in YEARS:
    for lab in ("all", "member", "casual"):
        pt_rows.append(peak_trough(None, y, lab))
ptdf = pd.DataFrame(pt_rows)
# agreement and consistency across years
agree = {}
for lab in ("all", "member", "casual"):
    a, b = ptdf[(ptdf.series == lab) & (ptdf.year == 2023)].iloc[0], ptdf[(ptdf.series == lab) & (ptdf.year == 2024)].iloc[0]
    agree[lab] = dict(peak_same_total=a.peak_month_total == b.peak_month_total, trough_same_total=a.trough_month_total == b.trough_month_total,
                      peak_same_per_day=a.peak_month_per_day == b.peak_month_per_day,
                      trough_same_per_day=a.trough_month_per_day == b.trough_month_per_day)
col = {"all": "trips_per_day", "member": "member_per_day", "casual": "casual_per_day"}
from scipy.stats import pearsonr, spearmanr
for lab in ("all", "member", "casual"):
    a, b = p23[col[lab]].values, p24[col[lab]].values
    agree[lab].update(pearson_r_monthly_per_day_2023_vs_2024=float(pearsonr(a, b)[0]),
                      spearman_rho_monthly_per_day_2023_vs_2024=float(spearmanr(a, b)[0]))
ptdf.to_csv(f"{TAB}/seasonality_peak_trough.csv", index=False)
FN["q1_seasonality"] = {"peak_trough": ptdf.to_dict("records"), "cross_year_agreement": agree,
                        "annual_totals": {int(y): int(piv[piv.year == y].trips.sum()) for y in YEARS},
                        "annual_growth_2024_over_2023": float(piv[piv.year == 2024].trips.sum() / piv[piv.year == 2023].trips.sum()),
                        "yoy_per_day_ratio_by_month": {MON[m - 1]: float(p24.trips_per_day[m] / p23.trips_per_day[m]) for m in range(1, 13)},
                        "yoy_per_day_ratio_range": [float((p24.trips_per_day / p23.trips_per_day).min()), float((p24.trips_per_day / p23.trips_per_day).max())],
                        "share_of_year_by_month": {int(y): {MON[m - 1]: float(piv[(piv.year == y) & (piv.month == m)].share_of_year.iloc[0]) for m in range(1, 13)} for y in YEARS},
                        "months_summer_jun_to_oct_share_of_year": {int(y): float(piv[(piv.year == y) & piv.month.between(6, 10)].share_of_year.sum()) for y in YEARS},
                        "months_dec_to_feb_share_of_year": {int(y): float(piv[(piv.year == y) & piv.month.isin([1, 2, 12])].share_of_year.sum()) for y in YEARS},
                        "monthly_per_day": {int(y): {MON[m - 1]: dict(mean=float(r.trips_per_day), ci=[float(r.daily_ci_lo), float(r.daily_ci_hi)])
                                                    for m, r in piv[piv.year == y].set_index("month").iterrows()} for y in YEARS}}

# ---- figure 1
fig, ax = plt.subplots(2, 2, figsize=(13, 8.6), constrained_layout=True)
sty = {2023: dict(color=C_Y23, ls="--", marker="s", ms=5), 2024: dict(color=C_Y24, ls="-", marker="o", ms=5)}
a = ax[0, 0]
for y in YEARS:
    g = piv[piv.year == y]
    a.plot(g.month, g.trips, label=str(y), **sty[y])
    i, j = g.trips.values.argmax(), g.trips.values.argmin()
    a.annotate(f"peak {MON[g.month.values[i] - 1]}\n{g.trips.values[i] / 1e6:.2f}M", (g.month.values[i], g.trips.values[i]), textcoords="offset points",
               xytext=(0, 9) if y == 2024 else (-4, 10), ha="center", fontsize=8.5, color=INK2)
    a.annotate(f"trough {MON[g.month.values[j] - 1]}\n{g.trips.values[j] / 1e6:.2f}M", (g.month.values[j], g.trips.values[j]), textcoords="offset points",
               xytext=(0, -26), ha="center", fontsize=8.5, color=INK2)
a.set_title("A  Monthly trips (census counts)"); a.set_ylabel("trips per month")
a.yaxis.set_major_formatter(FuncFormatter(millions)); a.set_ylim(0, None); a.legend(loc="upper left")
b = ax[0, 1]
for y, off in zip(YEARS, (-0.08, 0.08)):
    g = piv[piv.year == y]
    b.errorbar(g.month + off, g.trips_per_day, yerr=[g.trips_per_day - g.daily_ci_lo, g.daily_ci_hi - g.trips_per_day],
               capsize=2.5, elinewidth=1.2, label=str(y), **sty[y])
b.set_title("B  Mean trips per day, 95% bootstrap CI over days"); b.set_ylabel("trips per day")
b.yaxis.set_major_formatter(FuncFormatter(thousands)); b.set_ylim(0, None); b.legend(loc="upper left")
ymax = piv.trips.max() * 1.08
for k, y in enumerate(YEARS):
    c = ax[1, k]
    g = piv[piv.year == y]
    c.bar(g.month, g.member, color=C_MEM, edgecolor=SURF, linewidth=1.5, width=0.78, label="member")
    c.bar(g.month, g.casual, bottom=g.member, color=C_CAS, edgecolor=SURF, linewidth=1.5, width=0.78, label="casual")
    c.set_title(f"{'CD'[k]}  {y}: member vs casual (stacked)"); c.set_ylim(0, ymax); c.set_ylabel("trips per month")
    c.yaxis.set_major_formatter(FuncFormatter(millions)); c.legend(loc="upper left")
for c in ax.ravel():
    c.set_xticks(range(1, 13)); c.set_xticklabels(MON); c.set_xlim(0.4, 12.6)
    c.grid(axis="x", visible=False)
fig.suptitle("NYC Citi Bike: seasonality of trip volume, 2023 and 2024", x=0.01, ha="left", fontsize=13, fontweight="bold")
save(fig, "fig_monthly_volume.png")

# =================================================================== Q2 hour x day-of-week
log("Q2 hourly profiles")
isw = meta.is_weekend.values
wd_masks = {"weekday_all": ~isw,
            "weekday_excl_holidays": (~isw) & ~meta.is_holiday.values,
            "weekday_excl_holidays_dst": (~isw) & ~meta.is_holiday.values & ~meta.is_dst.values}
seasons = {"all_year": np.ones(len(meta), bool), "Jan-Feb": meta.month.isin([1, 2]).values, "Jul-Aug": meta.month.isin([7, 8]).values}
prof_rows, peak_rows = [], []


def add_peak_row(y, version, season, grp, H, mask):
    idx_ = np.where(mask)[0]
    Hs = H[idx_]
    bi = bidx(len(idx_))
    P = boot_profiles(Hs, bi)
    obs = Hs.mean(0)
    po, pb = pk(obs), pk(P)
    r = dict(year=y, version=version, season=season, daygroup=grp, n_days=len(idx_),
             daily_mean_trips=obs.sum(), daily_mean_ci_lo=ci(P.sum(1))[0], daily_mean_ci_hi=ci(P.sum(1))[1])
    hp, _ = find_peaks(np.r_[obs[0], obs, obs[-1]] if False else obs, prominence=0.05 * obs.max())
    r.update(n_prominent_peaks=len(hp), prominent_peak_hours=";".join(map(str, hp)))
    if grp == "weekday":
        for nm in ("am", "pm"):
            r[f"{nm}_peak_hour"] = int(po[nm]); r[f"{nm}_peak_mean_trips"] = po[nm + "v"]
            r[f"{nm}_peak_hour_boot_agree"] = float((pb[nm] == po[nm]).mean())
            lo, hi = ci(pb[nm + "v"]); r[f"{nm}_peak_ci_lo"], r[f"{nm}_peak_ci_hi"] = lo, hi
            lo, hi = ci(pb[nm + "_r"]); r[f"{nm}_peak_over_midday"], r[f"{nm}_ratio_ci_lo"], r[f"{nm}_ratio_ci_hi"] = po[nm + "_r"], lo, hi
        r["midday_mean_trips_11_14"] = po["mid"]
        r["pm_over_am_peak"] = po["pmv"] / po["amv"]
    else:
        r["weekend_peak_hour"] = int(po["wk"]); r["weekend_peak_mean_trips"] = po["wkv"]
        r["weekend_peak_hour_boot_agree"] = float((pb["wk"] == po["wk"]).mean())
        lo, hi = ci(pb["wkv"]); r["weekend_peak_ci_lo"], r["weekend_peak_ci_hi"] = lo, hi
        r["weekend_peak_share_of_day"] = po["wk_share"]
        lo, hi = ci(pb["wk_share"]); r["weekend_peak_share_ci_lo"], r["weekend_peak_share_ci_hi"] = lo, hi
    peak_rows.append(r)
    return obs, P


profiles = {}
for y in YEARS:
    ym = (meta.year == y).values
    for ver, wm in wd_masks.items():
        for sn, sm in seasons.items():
            if ver != "weekday_all" and sn != "all_year":
                continue
            m = ym & wm & sm
            obs, P = add_peak_row(y, ver, sn, "weekday", H_ALL, m)
            lo, hi = ci(P)
            profiles[(y, ver, sn, "weekday")] = (obs, lo, hi, m.sum())
            for h in HRS:
                prof_rows.append(dict(year=y, version=ver, season=sn, daygroup="weekday", hour=h, mean_trips=obs[h], ci_lo=lo[h], ci_hi=hi[h], n_days=int(m.sum())))
    for sn, sm in seasons.items():
        m = ym & isw & sm
        obs, P = add_peak_row(y, "weekend", sn, "weekend", H_ALL, m)
        lo, hi = ci(P)
        profiles[(y, "weekend", sn, "weekend")] = (obs, lo, hi, m.sum())
        for h in HRS:
            prof_rows.append(dict(year=y, version="weekend", season=sn, daygroup="weekend", hour=h, mean_trips=obs[h], ci_lo=lo[h], ci_hi=hi[h], n_days=int(m.sum())))
pd.DataFrame(prof_rows).to_csv(f"{TAB}/hourly_profiles_weekday_weekend.csv", index=False)
pkdf = pd.DataFrame(peak_rows)
pkdf.to_csv(f"{TAB}/peak_hours.csv", index=False)
FN["q2_hourly"] = {"peak_hours_primary": pkdf[(pkdf.season == "all_year") & pkdf.version.isin(["weekday_all", "weekend"])].to_dict("records"),
                   "sensitivity_holidays_dst": pkdf[(pkdf.season == "all_year") & pkdf.version.isin(["weekday_excl_holidays", "weekday_excl_holidays_dst"])].to_dict("records"),
                   "season_robustness": pkdf[pkdf.season.isin(["Jan-Feb", "Jul-Aug"])].to_dict("records")}
# did conclusions change under holiday exclusion?
chg = []
for y in YEARS:
    base = pkdf[(pkdf.year == y) & (pkdf.version == "weekday_all") & (pkdf.season == "all_year")].iloc[0]
    for ver in ("weekday_excl_holidays", "weekday_excl_holidays_dst"):
        s = pkdf[(pkdf.year == y) & (pkdf.version == ver)].iloc[0]
        chg.append(dict(year=y, version=ver, am_hour_same=bool(s.am_peak_hour == base.am_peak_hour), pm_hour_same=bool(s.pm_peak_hour == base.pm_peak_hour),
                        am_ratio_change=float(s.am_peak_over_midday - base.am_peak_over_midday), pm_ratio_change=float(s.pm_peak_over_midday - base.pm_peak_over_midday),
                        n_days_removed=int(base.n_days - s.n_days)))
FN["q2_hourly"]["sensitivity_comparison"] = chg

# ---- figure 2
fig, ax = plt.subplots(1, 2, figsize=(13, 4.8), sharey=True, constrained_layout=True)
for k, y in enumerate(YEARS):
    a = ax[k]
    for grp, colr in (("weekday", C_WD), ("weekend", C_WE)):
        key = (y, "weekday_all" if grp == "weekday" else "weekend", "all_year", grp)
        obs, lo, hi, n = profiles[key]
        a.fill_between(HRS, lo, hi, color=colr, alpha=0.22, lw=0)
        a.plot(HRS, obs, color=colr, label=f"{grp} (Mon-Fri, n={n} days)" if grp == "weekday" else f"weekend (Sat-Sun, n={n} days)")
    r = pkdf[(pkdf.year == y) & (pkdf.version == "weekday_all") & (pkdf.season == "all_year")].iloc[0]
    w = pkdf[(pkdf.year == y) & (pkdf.version == "weekend") & (pkdf.season == "all_year")].iloc[0]
    for hh, v_, c_, txt in ((r.am_peak_hour, r.am_peak_mean_trips, C_WD, f"{int(r.am_peak_hour):02d}:00"),
                            (r.pm_peak_hour, r.pm_peak_mean_trips, C_WD, f"{int(r.pm_peak_hour):02d}:00"),
                            (w.weekend_peak_hour, w.weekend_peak_mean_trips, C_WE, f"{int(w.weekend_peak_hour):02d}:00")):
        a.annotate(txt, (hh, v_), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9, color=INK2)
    a.set_title(f"{'AB'[k]}  {y}"); a.set_xlabel("hour of day (start time, local)"); a.set_xticks(range(0, 24, 2))
    a.yaxis.set_major_formatter(FuncFormatter(thousands)); a.set_ylim(0, None); a.legend(loc="upper left")
    a.grid(axis="x", visible=False)
ax[0].set_ylabel("mean trips per hour")
fig.suptitle("Mean trips by hour of day: weekday vs weekend (bands = 95% bootstrap CI, resampling dates, 2000 resamples)", x=0.01, ha="left", fontsize=12, fontweight="bold")
save(fig, "fig_hourly_weekday_vs_weekend.png")

# ---- heatmap dow x hour
rows = []
fig, ax = plt.subplots(1, 2, figsize=(13, 3.9), constrained_layout=True)
hm = {}
for y in YEARS:
    M = np.zeros((7, 24))
    for d in range(7):
        m = ((meta.year == y) & (meta.dow == d)).values
        M[d] = H_ALL[m].mean(0)
        for h in HRS:
            rows.append(dict(year=y, dow=d, dow_name=DOW[d], hour=h, mean_trips=M[d, h], n_days=int(m.sum())))
    hm[y] = M
vmax = max(v.max() for v in hm.values())
from matplotlib.colors import LinearSegmentedColormap
cmap = LinearSegmentedColormap.from_list("blue", ["#f3f8fe", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
for k, y in enumerate(YEARS):
    im = ax[k].imshow(hm[y], aspect="auto", cmap=cmap, vmin=0, vmax=vmax)
    ax[k].set_yticks(range(7)); ax[k].set_yticklabels(DOW); ax[k].set_xticks(range(0, 24, 2))
    ax[k].set_xlabel("hour of day"); ax[k].set_title(f"{'AB'[k]}  {y}"); ax[k].grid(False)
    for s in ax[k].spines.values():
        s.set_visible(False)
cb = fig.colorbar(im, ax=ax, shrink=0.9, pad=0.015); cb.set_label("mean trips per hour"); cb.formatter = FuncFormatter(thousands); cb.update_ticks()
fig.suptitle("Mean trips by day of week x hour (all riders; same colour scale in both years)", x=0.01, ha="left", fontsize=12, fontweight="bold")
save(fig, "fig_heatmap_dow_hour.png")
pd.DataFrame(rows).to_csv(f"{TAB}/heatmap_dow_hour_means.csv", index=False)

# day-of-week daily totals
wkblock_d = (meta.date.dt.isocalendar().year.astype(str) + "-" + meta.date.dt.isocalendar().week.astype(str).str.zfill(2)).values
rows = []
for y in YEARS:
    for d in range(7):
        m = ((meta.year == y) & (meta.dow == d)).values
        v = meta.day_total.values[m]
        lo, hi = ci(v[bidx(len(v))].mean(1))
        rows.append(dict(year=y, dow=d, dow_name=DOW[d], n_days=len(v), mean_daily_trips=v.mean(), ci_lo=lo, ci_hi=hi))
dowdf = pd.DataFrame(rows)
dowdf["ratio_to_year_mean_day"] = dowdf.mean_daily_trips / dowdf.groupby("year").mean_daily_trips.transform("mean")
# paired within-week measure: day total / mean of its own (complete, single-year) Mon-Sun week; bootstrap over weeks
wk_df = pd.DataFrame({"blk": wkblock_d, "year": meta.year.values, "dow": meta.dow.values, "t": meta.day_total.values})
rel_rows = []
for y in YEARS:
    w = wk_df[wk_df.year == y]
    full_wk = w.groupby("blk").filter(lambda g: len(g) == 7 and g.dow.nunique() == 7)
    R = full_wk.pivot(index="blk", columns="dow", values="t").values
    R = R / R.mean(1, keepdims=True)
    bm = R[bidx(len(R))].mean(1)
    lo, hi = ci(bm)
    for d in range(7):
        rel_rows.append(dict(year=y, dow=d, rel_to_week_mean=R[:, d].mean(), rel_ci_lo=lo[d], rel_ci_hi=hi[d], n_complete_weeks=len(R)))
dowdf = dowdf.merge(pd.DataFrame(rel_rows), on=["year", "dow"])
dowdf.to_csv(f"{TAB}/dow_daily_summary.csv", index=False)
wk = {}
for y in YEARS:
    ym = (meta.year == y).values
    a_, b_ = meta.day_total.values[ym & ~isw], meta.day_total.values[ym & isw]
    rr = a_[bidx(len(a_))].mean(1) / b_[bidx(len(b_))].mean(1)
    wk[int(y)] = dict(weekday_mean_daily=float(a_.mean()), weekend_mean_daily=float(b_.mean()), ratio=float(a_.mean() / b_.mean()), ratio_ci=[float(x) for x in ci(rr)])
FN["q2_dow"] = {"by_dow": dowdf.to_dict("records"), "weekday_vs_weekend_daily": wk}

# ---- seasonal robustness figure
fig, ax = plt.subplots(2, 2, figsize=(12, 7.6), sharey="row", constrained_layout=True)
for i, y in enumerate(YEARS):
    for j, grp in enumerate(("weekday", "weekend")):
        a = ax[i, j]
        for sn, colr in (("Jan-Feb", C_WIN), ("Jul-Aug", C_SUM)):
            obs, lo, hi, n = profiles[(y, "weekday_all" if grp == "weekday" else "weekend", sn, grp)]
            a.fill_between(HRS, lo, hi, color=colr, alpha=0.22, lw=0)
            a.plot(HRS, obs, color=colr, label=f"{sn} (n={n} days)")
        a.set_title(f"{y} {grp}"); a.set_xticks(range(0, 24, 2)); a.yaxis.set_major_formatter(FuncFormatter(thousands)); a.grid(axis="x", visible=False)
        a.set_ylim(0, None); a.legend(loc="upper left")
        if j == 0: a.set_ylabel("mean trips per hour")
        if i == 1: a.set_xlabel("hour of day")
fig.suptitle("Hourly profile in a winter vs a summer window (95% bootstrap CI over dates)", x=0.01, ha="left", fontsize=12, fontweight="bold")
save(fig, "fig_hourly_seasonal_robustness.png")

# =================================================================== Q3 member vs casual
log("Q3 member vs casual")
HT = {"member": H_MEM, "casual": H_CAS}
prof3, jsd_rows, nprof_rows = [], [], []


def norm(P):
    return P / P.sum(-1, keepdims=True)


for y in YEARS:
    ym = (meta.year == y).values
    for grp, gm in (("weekday", ~isw), ("weekend", isw)):
        ix = np.where(ym & gm)[0]
        bi = bidx(len(ix))
        Pb = {t: boot_profiles(HT[t][ix], bi) for t in HT}      # same date resamples for both types (paired)
        Po = {t: HT[t][ix].mean(0) for t in HT}
        for t in HT:
            lo, hi = ci(Pb[t]); nlo, nhi = ci(norm(Pb[t]))
            for h in HRS:
                prof3.append(dict(year=y, daygroup=grp, member_casual=t, hour=h, mean_trips=Po[t][h], ci_lo=lo[h], ci_hi=hi[h],
                                  share_of_daily=norm(Po[t])[h], share_ci_lo=nlo[h], share_ci_hi=nhi[h], n_days=len(ix)))
        jb = jensenshannon(norm(Pb["member"]), norm(Pb["casual"]), base=2, axis=1)
        jo = float(jensenshannon(norm(Po["member"]), norm(Po["casual"]), base=2))
        rb = np.array([np.corrcoef(norm(Pb["member"])[i], norm(Pb["casual"])[i])[0, 1] for i in range(B)])
        ro = float(np.corrcoef(norm(Po["member"]), norm(Po["casual"]))[0, 1] if False else np.corrcoef(norm(Po["member"]), norm(Po["casual"]))[0, 1])
        jsd_rows.append(dict(year=y, comparison=f"member_vs_casual_{grp}", jsd_base2=jo, jsd_ci_lo=ci(jb)[0], jsd_ci_hi=ci(jb)[1],
                             pearson_r=ro, r_ci_lo=ci(rb)[0], r_ci_hi=ci(rb)[1], n_days=len(ix)))
        for t in HT:
            p = pk(Pb[t]); o = pk(Po[t])
            nprof_rows.append(dict(year=y, daygroup=grp, member_casual=t, am_peak_hour=int(o["am"]), pm_peak_hour=int(o["pm"]), weekend_peak_hour=int(o["wk"]),
                                   am_over_midday=o["am_r"], am_ratio_ci_lo=ci(p["am_r"])[0], am_ratio_ci_hi=ci(p["am_r"])[1],
                                   pm_over_midday=o["pm_r"], pm_ratio_ci_lo=ci(p["pm_r"])[0], pm_ratio_ci_hi=ci(p["pm_r"])[1],
                                   wk_peak_hour_boot_agree=float((p["wk"] == o["wk"]).mean()), am_hour_boot_agree=float((p["am"] == o["am"]).mean()),
                                   pm_hour_boot_agree=float((p["pm"] == o["pm"]).mean()),
                                   am_peak_distinct=bool(any(6 <= h <= 10 for h in find_peaks(Po[t], prominence=0.05 * Po[t].max())[0])),
                                   n_prominent_peaks=len(find_peaks(Po[t], prominence=0.05 * Po[t].max())[0]),
                                   prominent_peak_hours=";".join(map(str, find_peaks(Po[t], prominence=0.05 * Po[t].max())[0]))))
    # within-type weekday vs weekend shape (independent resamples of the two day groups)
    iw, ie = np.where(ym & ~isw)[0], np.where(ym & isw)[0]
    bw, be = bidx(len(iw)), bidx(len(ie))
    for t in HT:
        Pw, Pe = norm(boot_profiles(HT[t][iw], bw)), norm(boot_profiles(HT[t][ie], be))
        jo = float(jensenshannon(norm(HT[t][iw].mean(0)), norm(HT[t][ie].mean(0)), base=2))
        jb = jensenshannon(Pw, Pe, base=2, axis=1)
        jsd_rows.append(dict(year=y, comparison=f"{t}_weekday_vs_weekend", jsd_base2=jo, jsd_ci_lo=ci(jb)[0], jsd_ci_hi=ci(jb)[1],
                             pearson_r=float(np.corrcoef(norm(HT[t][iw].mean(0)), norm(HT[t][ie].mean(0)))[0, 1]),
                             r_ci_lo=np.nan, r_ci_hi=np.nan, n_days=len(iw) + len(ie)))
pd.DataFrame(prof3).to_csv(f"{TAB}/member_casual_hourly_profiles.csv", index=False)
pd.DataFrame(jsd_rows).to_csv(f"{TAB}/member_casual_profile_shape.csv", index=False)
pd.DataFrame(nprof_rows).to_csv(f"{TAB}/member_casual_peak_hours.csv", index=False)
p3 = pd.DataFrame(prof3)

# ---- figure 3: normalised (cols 0-1) and raw (cols 2-3)
fig, ax = plt.subplots(2, 4, figsize=(20, 8.2), constrained_layout=True)
for i, y in enumerate(YEARS):
    for j, (kind, grp) in enumerate((("share", "weekday"), ("share", "weekend"), ("raw", "weekday"), ("raw", "weekend"))):
        a = ax[i, j]
        for t, colr in (("member", C_MEM), ("casual", C_CAS)):
            g = p3[(p3.year == y) & (p3.daygroup == grp) & (p3.member_casual == t)].sort_values("hour")
            if kind == "share":
                a.fill_between(g.hour, g.share_ci_lo * 100, g.share_ci_hi * 100, color=colr, alpha=0.22, lw=0)
                a.plot(g.hour, g.share * 100 if False else g.share_of_daily * 100, color=colr, label=t)
            else:
                a.fill_between(g.hour, g.ci_lo, g.ci_hi, color=colr, alpha=0.22, lw=0)
                a.plot(g.hour, g.mean_trips, color=colr, label=t)
        a.set_title(f"{y} {grp}: " + ("share of own daily trips" if kind == "share" else "mean trips per hour"))
        a.set_xticks(range(0, 24, 3)); a.grid(axis="x", visible=False); a.set_ylim(0, None)
        if kind == "share": a.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        else: a.yaxis.set_major_formatter(FuncFormatter(thousands))
        if i == 1: a.set_xlabel("hour of day")
        if i == 0 and j == 0: a.legend(loc="upper left")
fig.suptitle("Member vs casual hourly profiles. Left: each type normalised to its own daily total (shape). Right: raw mean counts. Bands: 95% bootstrap CI over dates",
             x=0.01, ha="left", fontsize=12, fontweight="bold")
save(fig, "fig_member_vs_casual_hourly.png")

# ---- figure 4: casual share by month
fig, ax = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
a = ax[0]
for y in YEARS:
    g = piv[piv.year == y]
    a.errorbar(g.month + (-0.07 if y == 2023 else 0.07), g.casual_share * 100, yerr=[(g.casual_share - g.casual_share_ci_lo) * 100, (g.casual_share_ci_hi - g.casual_share) * 100],
               capsize=2.5, elinewidth=1.1, label=str(y), **sty[y])
a.set_title("A  Casual share of monthly trips (95% bootstrap CI over days)"); a.set_ylabel("casual share of trips")
a.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%")); a.set_ylim(0, None); a.legend(loc="upper left")
a = ax[1]
for y in YEARS:
    g = piv[piv.year == y]
    ls = "--" if y == 2023 else "-"
    a.plot(g.month, g.member_per_day, color=C_MEM, ls=ls, marker="s" if y == 2023 else "o", ms=5, label=f"member {y}")
    a.plot(g.month, g.casual_per_day, color=C_CAS, ls=ls, marker="s" if y == 2023 else "o", ms=5, label=f"casual {y}")
a.set_title("B  Mean trips per day by rider type"); a.set_ylabel("trips per day"); a.yaxis.set_major_formatter(FuncFormatter(thousands))
a.set_ylim(0, None); a.legend(loc="upper left", ncol=2)
for c in ax:
    c.set_xticks(range(1, 13)); c.set_xticklabels(MON); c.grid(axis="x", visible=False)
save(fig, "fig_member_vs_casual_seasonal.png")
piv[["year", "month", "month_name", "member", "casual", "trips", "casual_share", "casual_share_ci_lo", "casual_share_ci_hi",
     "member_per_day", "member_per_day_ci_lo", "member_per_day_ci_hi", "casual_per_day", "casual_per_day_ci_lo", "casual_per_day_ci_hi"]
    ].to_csv(f"{TAB}/casual_share_by_month.csv", index=False)

# ---- summary table: weekend share, peak-window share, median duration
log("Q3 summary stats")
wkblock = (meta.date.dt.isocalendar().year.astype(str) + "-" + meta.date.dt.isocalendar().week.astype(str).str.zfill(2)).values
sum_rows = []
win = np.r_[7:10, 16:19]
for y in YEARS:
    ym = (meta.year == y).values
    for t in ("member", "casual", "all"):
        H = {"member": H_MEM, "casual": H_CAS, "all": H_ALL}[t]
        tot = H.sum(1)
        # weekend share, bootstrap over ISO-week blocks within year
        wb = pd.DataFrame({"blk": wkblock[ym], "tot": tot[ym], "we": np.where(isw[ym], tot[ym], 0.0)}).groupby("blk").sum()
        bt, bw_ = wb.tot.values, wb.we.values
        bi = bidx(len(bt))
        ws = bw_[bi].sum(1) / bt[bi].sum(1)
        # weekday peak-window share
        iw = np.where(ym & ~isw)[0]
        pwin, ptot = H[iw][:, win].sum(1), H[iw].sum(1)
        bi2 = bidx(len(iw))
        ps = pwin[bi2].sum(1) / ptot[bi2].sum(1)
        # also fixed-window shares for the AM and PM parts separately
        am = H[iw][:, 7:10].sum(1); pm_ = H[iw][:, 16:19].sum(1)
        sum_rows.append(dict(year=y, member_casual=t, trips=int(tot[ym].sum()), weekend_trip_share=bw_.sum() / bt.sum(), weekend_share_ci_lo=ci(ws)[0], weekend_share_ci_hi=ci(ws)[1],
                             n_week_blocks=len(bt), weekend_days_share_of_days=float(isw[ym].mean()),
                             weekday_peakwindow_share=pwin.sum() / ptot.sum(), peakwindow_ci_lo=ci(ps)[0], peakwindow_ci_hi=ci(ps)[1],
                             weekday_am_window_share=am.sum() / ptot.sum(), weekday_pm_window_share=pm_.sum() / ptot.sum(), n_weekdays=len(iw)))
sdf = pd.DataFrame(sum_rows)
# durations: 1M random clean trips per type-year, fixed seed; bootstrap CI of the median
con = duckdb.connect("/app/work/flagged.duckdb", read_only=True)
full = pd.read_csv(f"{TAB}/duration_by_type.csv")
full = full[full.rideable_type == "ALL"]
drow = []
rg = np.random.default_rng(SEED + 1)
for y in YEARS:
    for t in ("member", "casual"):
        v = con.execute("SELECT m FROM (SELECT dur_s/60.0 AS m FROM f WHERE fm=0 AND year(started_at)=? AND member_casual=?) USING SAMPLE reservoir(1000000 ROWS) REPEATABLE (42)",
                        [y, t]).fetchnumpy()
        v = np.asarray(list(v.values())[0], float)
        meds = np.array([np.median(v[rg.integers(0, len(v), len(v))]) for _ in range(500)])
        f = full[(full.year.astype(str) == str(y)) & (full.member_casual == t)].iloc[0]
        drow.append(dict(year=y, member_casual=t, sample_n=len(v), median_min_sample=float(np.median(v)), median_ci_lo=ci(meds)[0], median_ci_hi=ci(meds)[1],
                         p25_min_sample=float(np.percentile(v, 25)), p75_min_sample=float(np.percentile(v, 75)),
                         median_min_full_population=float(f.median_min), mean_min_full_population=float(f.mean_min), full_population_trips=int(f.trips)))
        log(f"duration {y} {t} done")
ddf = pd.DataFrame(drow)
out = sdf.merge(ddf, on=["year", "member_casual"], how="left")
out.to_csv(f"{TAB}/member_casual_summary.csv", index=False)

# ratio casual/member medians (bootstrap not repeated: report with CI propagated by pairing the sample CIs conservatively)
dur_ratio = {}
for y in YEARS:
    c_, m_ = ddf[(ddf.year == y) & (ddf.member_casual == "casual")].iloc[0], ddf[(ddf.year == y) & (ddf.member_casual == "member")].iloc[0]
    dur_ratio[int(y)] = dict(casual_over_member_median=float(c_.median_min_sample / m_.median_min_sample),
                             conservative_range=[float(c_.median_ci_lo / m_.median_ci_hi), float(c_.median_ci_hi / m_.median_ci_lo)])
FN["q3_member_casual"] = {"summary": out.to_dict("records"), "profile_shape": pd.DataFrame(jsd_rows).to_dict("records"),
                          "peak_hours": pd.DataFrame(nprof_rows).to_dict("records"), "duration_ratio": dur_ratio,
                          "casual_share_by_month": {int(y): {MON[m - 1]: float(piv[(piv.year == y) & (piv.month == m)].casual_share.iloc[0]) for m in range(1, 13)} for y in YEARS},
                          "casual_share_annual": {int(y): float(piv[piv.year == y].casual.sum() / piv[piv.year == y].trips.sum()) for y in YEARS}}

# ---- checks: figure totals equal monthly_counts.csv
chk = {}
for y in YEARS:
    chk[int(y)] = bool(piv[piv.year == y].trips.sum() == mc[mc.year == y].trips.sum())
FN["checks"] = {"figure_monthly_totals_equal_monthly_counts_csv": chk,
                "sum_all_tables_equal_total": bool(hd.trips.sum() == mc.trips.sum() == dc.trips.sum()), "total_clean_trips": TOTAL_TRIPS}


def clean(o):
    if isinstance(o, dict): return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, np.ndarray): return clean(o.tolist())
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating, float)): return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)): return bool(o)
    return o


with open(f"{TAB}/findings_numbers.json", "w") as f:
    json.dump(clean(FN), f, indent=1)
log("findings_numbers.json written; ALL DONE")
