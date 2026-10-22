#!/usr/bin/env python3
"""Clean Citi Bike 2023-2024 trips and build aggregate tables (DuckDB).

Usage: python3 -I /app/code/02_clean_aggregate.py
Inputs : /app/parquet/trips_{2023,2024}.parquet  (from 01_acquire.py)
Outputs: /app/output/tables/*.csv, anomalies.md, checks.json ; work db /app/work/flagged.duckdb

Cleaning rules (ordered; a row is attributed to the FIRST rule it fails):
  R1 missing/unparseable started_at or ended_at
  R2 duplicate ride_id (keep one copy: smallest source_file, then raw timestamps)
  R3 duration <= 0 s
  R4 0 < duration < 60 s   (primary analysis excludes; sensitivity columns keep them)
  R5 duration > 24 h
  R6 started_at outside 2023-01-01 .. 2024-12-31
  R7 member_casual not in ('member','casual')
Timestamps are NYC local wall-clock (naive); no tz shift is applied.
"""
import json
import os
import time

import duckdb
import pandas as pd


def md(df, index=False):
    if index:
        df = df.reset_index()
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(str(v.date()) if hasattr(v, "date") else str(v) for v in r) + " |")
    return "\n".join(out)


APP = "/app"
OUT = f"{APP}/output/tables"
WORK = f"{APP}/work"
os.makedirs(OUT, exist_ok=True)
os.makedirs(WORK, exist_ok=True)
DB = f"{WORK}/flagged.duckdb"
if os.path.exists(DB):
    os.remove(DB)

con = duckdb.connect(DB)
con.execute("SET threads=16")
con.execute("SET memory_limit='4GB'")
con.execute(f"SET temp_directory='{WORK}/tmp'")
con.execute("SET preserve_insertion_order=false")
t0 = time.time()


def log(msg):
    print(f"[{time.time()-t0:7.1f}s] {msg}", flush=True)


RULES = [
    ("R1", "missing/unparseable started_at or ended_at"),
    ("R2", "duplicate ride_id (extra copies; one kept)"),
    ("R3", "duration <= 0 s"),
    ("R4", "duration < 60 s (very short; sensitivity rule)"),
    ("R5", "duration > 24 h"),
    ("R6", "started_at outside 2023-01-01..2024-12-31"),
    ("R7", "member_casual not in {member, casual}"),
]

# ---------------------------------------------------------------- build flagged table
con.execute("""
CREATE VIEW raw AS
SELECT 2023 AS fy, * FROM read_parquet('/app/parquet/trips_2023.parquet')
UNION ALL
SELECT 2024 AS fy, * FROM read_parquet('/app/parquet/trips_2024.parquet')
""")

log("finding duplicate ride_id candidates via hash")
con.execute("""
CREATE TABLE dup_hash AS
SELECT h FROM (SELECT hash(ride_id) AS h FROM raw WHERE ride_id IS NOT NULL) GROUP BY h HAVING count(*) > 1
""")
log(f"dup hash candidates: {con.execute('select count(*) from dup_hash').fetchone()[0]}")

COLS = """
  fy,
  CAST(regexp_extract(source_file, '(\\d{6})-citibike-tripdata_', 1) AS INTEGER) AS file_ym,
  rideable_type, member_casual, started_at, ended_at,
  epoch(ended_at) - epoch(started_at) AS dur_s,
  (start_station_id IS NULL OR start_station_id = '') AS start_stn_missing,
  (end_station_id IS NULL OR end_station_id = '') AS end_stn_missing
"""
# non-candidate rows: not duplicates
con.execute(f"""
CREATE TABLE base AS
SELECT {COLS}, FALSE AS dup_extra FROM raw
WHERE ride_id IS NULL OR hash(ride_id) NOT IN (SELECT h FROM dup_hash)
""")
# candidate rows: exact duplicate resolution on the real ride_id
con.execute(f"""
INSERT INTO base
SELECT {COLS},
  row_number() OVER (PARTITION BY ride_id ORDER BY source_file, started_at_raw, ended_at_raw) > 1 AS dup_extra
FROM raw
WHERE ride_id IS NOT NULL AND hash(ride_id) IN (SELECT h FROM dup_hash)
""")
log(f"base rows: {con.execute('select count(*) from base').fetchone()[0]}")

# bitmask of independently failed rules (bit i-1 for R_i); first (lowest) bit = attributed rule
con.execute("""
CREATE TABLE flagged AS
SELECT *,
  (CASE WHEN started_at IS NULL OR ended_at IS NULL THEN 1 ELSE 0 END)
  | (CASE WHEN dup_extra THEN 2 ELSE 0 END)
  | (CASE WHEN dur_s <= 0 THEN 4 ELSE 0 END)
  | (CASE WHEN dur_s < 60 THEN 8 ELSE 0 END)
  | (CASE WHEN dur_s > 86400 THEN 16 ELSE 0 END)
  | (CASE WHEN started_at IS NOT NULL AND (started_at < TIMESTAMP '2023-01-01' OR started_at >= TIMESTAMP '2025-01-01') THEN 32 ELSE 0 END)
  | (CASE WHEN member_casual IS NULL OR member_casual NOT IN ('member','casual') THEN 64 ELSE 0 END) AS fm
FROM base
""")
con.execute("DROP TABLE base")
con.execute("DROP VIEW raw")
log("flagged table built")

# first-rule index (1..7) or 0 when clean
con.execute("""
CREATE VIEW f AS
SELECT *, CASE WHEN fm=0 THEN 0
  WHEN fm&1>0 THEN 1 WHEN fm&2>0 THEN 2 WHEN fm&4>0 THEN 3 WHEN fm&8>0 THEN 4
  WHEN fm&16>0 THEN 5 WHEN fm&32>0 THEN 6 ELSE 7 END AS first_rule
FROM flagged
""")
# 'clean' (primary): fm=0.  'clean_incl_short' (sensitivity): fm=0 or fm==8 (fails only R4)
CLEAN = "fm = 0"
CLEAN_SENS = "(fm = 0 OR fm = 8)"

# ---------------------------------------------------------------- cleaning summary
def cleaning_summary():
    raw_by_year = dict(con.execute("SELECT fy, count(*) FROM f GROUP BY 1").fetchall())
    rows = []
    for scope, yrs in (("all", None), ("2023", 2023), ("2024", 2024)):
        where = "" if yrs is None else f"WHERE fy={yrs}"
        raw_n = sum(raw_by_year.values()) if yrs is None else raw_by_year[yrs]
        seq = dict(con.execute(f"SELECT first_rule, count(*) FROM f {where} GROUP BY 1").fetchall())
        for i, (rid, desc) in enumerate(RULES, start=1):
            ind = con.execute(f"SELECT count(*) FROM f {where} {'AND' if where else 'WHERE'} (fm & {1 << (i-1)}) > 0").fetchone()[0]
            sq = seq.get(i, 0)
            rows.append(dict(scope=scope, rule=rid, description=desc, rows_removed_independent=ind,
                             rows_removed_sequential=sq, pct_of_raw_independent=100 * ind / raw_n,
                             pct_of_raw_sequential=100 * sq / raw_n))
        clean_n = seq.get(0, 0)
        rows.append(dict(scope=scope, rule="TOTAL_REMOVED", description="all rules, sequential",
                         rows_removed_independent=None, rows_removed_sequential=raw_n - clean_n,
                         pct_of_raw_independent=None, pct_of_raw_sequential=100 * (raw_n - clean_n) / raw_n))
        rows.append(dict(scope=scope, rule="RAW_ROWS", description="rows in input", rows_removed_independent=None,
                         rows_removed_sequential=raw_n, pct_of_raw_independent=None, pct_of_raw_sequential=100.0))
        rows.append(dict(scope=scope, rule="CLEAN_ROWS", description="rows kept (primary analysis)",
                         rows_removed_independent=None, rows_removed_sequential=clean_n,
                         pct_of_raw_independent=None, pct_of_raw_sequential=100 * clean_n / raw_n))
        sens = con.execute(f"SELECT count(*) FROM f {where} {'AND' if where else 'WHERE'} {CLEAN_SENS}").fetchone()[0]
        rows.append(dict(scope=scope, rule="CLEAN_ROWS_INCL_SHORT", description="rows kept if R4 is not applied (sensitivity)",
                         rows_removed_independent=None, rows_removed_sequential=sens,
                         pct_of_raw_independent=None, pct_of_raw_sequential=100 * sens / raw_n))
    df = pd.DataFrame(rows)
    df.to_csv(f"{OUT}/cleaning_summary.csv", index=False)
    return df


cs = cleaning_summary()
log("cleaning_summary.csv written")
print(cs[cs.scope == "all"].to_string())

# per-month raw vs clean (by source-file month) and by start month
pm = con.execute(f"""
SELECT file_ym AS file_month,
  count(*) AS raw_rows,
  count(*) FILTER (WHERE first_rule=1) AS removed_R1,
  count(*) FILTER (WHERE first_rule=2) AS removed_R2,
  count(*) FILTER (WHERE first_rule=3) AS removed_R3,
  count(*) FILTER (WHERE first_rule=4) AS removed_R4,
  count(*) FILTER (WHERE first_rule=5) AS removed_R5,
  count(*) FILTER (WHERE first_rule=6) AS removed_R6,
  count(*) FILTER (WHERE first_rule=7) AS removed_R7,
  count(*) FILTER (WHERE fm=0) AS clean_rows,
  100.0 * count(*) FILTER (WHERE fm=0) / count(*) AS clean_pct
FROM f GROUP BY 1 ORDER BY 1
""").df()
st = con.execute(f"""
SELECT year(started_at)*100+month(started_at) AS file_month, count(*) AS clean_rows_by_start_month
FROM f WHERE fm=0 GROUP BY 1
""").df()
pm = pm.merge(st, on="file_month", how="outer").sort_values("file_month")
pm.to_csv(f"{OUT}/cleaning_per_month.csv", index=False)
log("cleaning_per_month.csv written")

# ---------------------------------------------------------------- aggregates
# (a) monthly counts (primary clean; plus sensitivity count incl. short trips)
con.execute(f"""
COPY (
SELECT year(started_at) AS year, month(started_at) AS month, member_casual,
  count(*) FILTER (WHERE fm=0) AS trips,
  count(*) AS trips_incl_short,
  avg(dur_s/60.0) FILTER (WHERE fm=0) AS mean_duration_min,
  median(dur_s/60.0) FILTER (WHERE fm=0) AS median_duration_min
FROM f WHERE {CLEAN_SENS}
GROUP BY ALL ORDER BY 1,2,3) TO '{OUT}/monthly_counts.csv' (HEADER)
""")

# (b) daily counts on a full date x user-type grid (zeros explicit)
con.execute(f"""
COPY (
WITH grid AS (
  SELECT CAST(d AS DATE) AS date, m AS member_casual
  FROM generate_series(DATE '2023-01-01', DATE '2024-12-31', INTERVAL 1 DAY) AS g(d),
       (VALUES ('member'),('casual')) AS u(m)),
agg AS (
  SELECT CAST(started_at AS DATE) AS date, member_casual,
    count(*) FILTER (WHERE fm=0) AS trips, count(*) AS trips_incl_short
  FROM f WHERE {CLEAN_SENS} GROUP BY 1,2)
SELECT g.date, year(g.date) AS year, month(g.date) AS month,
  isodow(g.date)-1 AS dow, strftime(g.date, '%a') AS dow_name, isodow(g.date) >= 6 AS is_weekend,
  g.member_casual, coalesce(a.trips,0) AS trips, coalesce(a.trips_incl_short,0) AS trips_incl_short
FROM grid g LEFT JOIN agg a USING (date, member_casual)
ORDER BY 1,7) TO '{OUT}/daily_counts.csv' (HEADER)
""")

# (c) date x hour x user type (full grid, zeros explicit)
con.execute(f"""
COPY (
WITH grid AS (
  SELECT CAST(d AS DATE) AS date, h AS hour, m AS member_casual
  FROM generate_series(DATE '2023-01-01', DATE '2024-12-31', INTERVAL 1 DAY) AS g(d),
       range(0,24) AS hh(h), (VALUES ('member'),('casual')) AS u(m)),
agg AS (
  SELECT CAST(started_at AS DATE) AS date, hour(started_at) AS hour, member_casual,
    count(*) FILTER (WHERE fm=0) AS trips, count(*) AS trips_incl_short
  FROM f WHERE {CLEAN_SENS} GROUP BY 1,2,3)
SELECT g.date, year(g.date) AS year, month(g.date) AS month,
  isodow(g.date)-1 AS dow, strftime(g.date, '%a') AS dow_name, isodow(g.date) >= 6 AS is_weekend,
  g.hour, g.member_casual, coalesce(a.trips,0) AS trips, coalesce(a.trips_incl_short,0) AS trips_incl_short
FROM grid g LEFT JOIN agg a USING (date, hour, member_casual)
ORDER BY 1,7,8) TO '{OUT}/hourly_dow.csv' (HEADER)
""")
log("monthly/daily/hourly tables written")

# (d) duration summary by year x user type x rideable type (+ pooled rows)
con.execute("CREATE TABLE cl AS SELECT year(started_at) AS y, member_casual AS u, rideable_type AS r, dur_s/60.0 AS m FROM f WHERE fm=0")
parts = []
for gcols in (["y", "u", "r"], ["y", "u"], ["y", "r"], ["y"], ["u", "r"], ["u"], ["r"], []):
    sel = ", ".join(f"CAST({c} AS VARCHAR) AS {n}" if c in gcols else f"'ALL' AS {n}"
                    for c, n in (("y", "year"), ("u", "member_casual"), ("r", "rideable_type")))
    grp = f"GROUP BY {', '.join(gcols)}" if gcols else ""
    parts.append(con.execute(f"""
      SELECT {sel}, count(*) AS trips, avg(m) AS mean_min, stddev(m) AS sd_min,
        quantile_cont(m, [0.05,0.25,0.5,0.75,0.95,0.99]) AS q, max(m) AS max_min
      FROM cl {grp}""").df())
    log(f"duration set {gcols or 'ALL'} done")
d = pd.concat(parts, ignore_index=True)
for i, nm in enumerate(["p05_min", "p25_min", "median_min", "p75_min", "p95_min", "p99_min"]):
    d[nm] = d.q.map(lambda v: v[i])
d = d.drop(columns="q")[["year", "member_casual", "rideable_type", "trips", "mean_min", "sd_min", "p05_min", "p25_min",
                         "median_min", "p75_min", "p95_min", "p99_min", "max_min"]]
d = d.sort_values(["year", "member_casual", "rideable_type"])
d.to_csv(f"{OUT}/duration_by_type.csv", index=False)
con.execute("DROP TABLE cl")
log("duration_by_type.csv written")

# bike type / user mix + missing-station descriptive stats (long format)
mix = con.execute(f"""
WITH c AS (SELECT year(started_at) AS year, month(started_at) AS month, rideable_type, member_casual,
             start_stn_missing, end_stn_missing FROM f WHERE fm=0)
SELECT 'rideable_by_month' AS view, year, month, rideable_type AS category, count(*) AS trips,
  count(*) * 1.0 / sum(count(*)) OVER (PARTITION BY year, month) AS share
FROM c GROUP BY year, month, rideable_type
UNION ALL
SELECT 'member_share_by_month', year, month, member_casual, count(*),
  count(*) * 1.0 / sum(count(*)) OVER (PARTITION BY year, month)
FROM c GROUP BY year, month, member_casual
UNION ALL
SELECT 'member_share_by_year', year, NULL, member_casual, count(*),
  count(*) * 1.0 / sum(count(*)) OVER (PARTITION BY year)
FROM c GROUP BY year, member_casual
UNION ALL
SELECT 'rideable_by_year', year, NULL, rideable_type, count(*),
  count(*) * 1.0 / sum(count(*)) OVER (PARTITION BY year)
FROM c GROUP BY year, rideable_type
UNION ALL
SELECT 'rideable_by_year_and_user', year, NULL, member_casual || '/' || rideable_type, count(*),
  count(*) * 1.0 / sum(count(*)) OVER (PARTITION BY year, member_casual)
FROM c GROUP BY year, member_casual, rideable_type
UNION ALL
SELECT 'start_station_missing_by_month', year, month, 'missing', count(*) FILTER (WHERE start_stn_missing),
  avg(CAST(start_stn_missing AS DOUBLE)) FROM c GROUP BY year, month
UNION ALL
SELECT 'end_station_missing_by_month', year, month, 'missing', count(*) FILTER (WHERE end_stn_missing),
  avg(CAST(end_stn_missing AS DOUBLE)) FROM c GROUP BY year, month
UNION ALL
SELECT 'start_station_missing_by_rideable_year', year, NULL, rideable_type, count(*) FILTER (WHERE start_stn_missing),
  avg(CAST(start_stn_missing AS DOUBLE)) FROM c GROUP BY year, rideable_type
UNION ALL
SELECT 'end_station_missing_by_rideable_year', year, NULL, rideable_type, count(*) FILTER (WHERE end_stn_missing),
  avg(CAST(end_stn_missing AS DOUBLE)) FROM c GROUP BY year, rideable_type
ORDER BY 1,2,3,4
""").df()
mix.to_csv(f"{OUT}/bike_type_and_user_mix.csv", index=False)
log("bike_type_and_user_mix.csv written")

# ---------------------------------------------------------------- checks
checks = {}
raw_n = int(cs[(cs.scope == "all") & (cs.rule == "RAW_ROWS")].rows_removed_sequential.iloc[0])
clean_n = int(cs[(cs.scope == "all") & (cs.rule == "CLEAN_ROWS")].rows_removed_sequential.iloc[0])
seq_sum = int(cs[(cs.scope == "all") & cs.rule.isin([r for r, _ in RULES])].rows_removed_sequential.sum())
checks["raw_rows"] = raw_n
checks["sequential_removed_sum"] = seq_sum
checks["clean_rows"] = clean_n
checks["seq_plus_clean_equals_raw"] = (seq_sum + clean_n == raw_n)
assert checks["seq_plus_clean_equals_raw"]
for y in ("2023", "2024"):
    s = int(cs[(cs.scope == y) & cs.rule.isin([r for r, _ in RULES])].rows_removed_sequential.sum())
    c = int(cs[(cs.scope == y) & (cs.rule == "CLEAN_ROWS")].rows_removed_sequential.iloc[0])
    r = int(cs[(cs.scope == y) & (cs.rule == "RAW_ROWS")].rows_removed_sequential.iloc[0])
    assert s + c == r, y
checks["per_month_raw_sum_equals_raw"] = int(pm.raw_rows.sum()) == raw_n
assert checks["per_month_raw_sum_equals_raw"]

mo = pd.read_csv(f"{OUT}/monthly_counts.csv")
da = pd.read_csv(f"{OUT}/daily_counts.csv", parse_dates=["date"])
ho = pd.read_csv(f"{OUT}/hourly_dow.csv", parse_dates=["date"])
dm = da.groupby(["year", "month", "member_casual"], as_index=False)[["trips", "trips_incl_short"]].sum()
mm = mo.merge(dm, on=["year", "month", "member_casual"], suffixes=("_m", "_d"))
checks["monthly_equals_sum_daily"] = bool(len(mm) == len(mo) and (mm.trips_m == mm.trips_d).all()
                                          and (mm.trips_incl_short_m == mm.trips_incl_short_d).all())
hd = ho.groupby(["date", "member_casual"], as_index=False)[["trips", "trips_incl_short"]].sum()
dd = da.merge(hd, on=["date", "member_casual"], suffixes=("_d", "_h"))
checks["daily_equals_sum_hourly"] = bool((dd.trips_d == dd.trips_h).all() and (dd.trips_incl_short_d == dd.trips_incl_short_h).all())
checks["daily_total_trips_equals_clean_rows"] = int(da.trips.sum()) == clean_n
checks["distinct_dates_daily"] = int(da.date.nunique())
checks["distinct_dates_by_year"] = {int(k): int(v) for k, v in da.groupby("year").date.nunique().items()}
checks["date_min"] = str(da.date.min().date())
checks["date_max"] = str(da.date.max().date())
checks["hourly_rows"] = len(ho)
checks["hourly_rows_expected"] = 731 * 24 * 2
checks["monthly_total_trips_equals_clean_rows"] = int(mo.trips.sum()) == clean_n
checks["clean_started_at_min"] = str(con.execute("SELECT min(started_at) FROM f WHERE fm=0").fetchone()[0])
checks["clean_started_at_max"] = str(con.execute("SELECT max(started_at) FROM f WHERE fm=0").fetchone()[0])
for k in ("monthly_equals_sum_daily", "daily_equals_sum_hourly", "daily_total_trips_equals_clean_rows",
          "monthly_total_trips_equals_clean_rows"):
    assert checks[k], k
assert checks["distinct_dates_daily"] == 731 and checks["distinct_dates_by_year"] == {2023: 365, 2024: 366}
assert checks["hourly_rows"] == checks["hourly_rows_expected"]

# ---------------------------------------------------------------- anomalies note
dt = da.groupby("date", as_index=False).trips.sum()
med = dt.trips.median()
low = dt[dt.trips < 0.10 * med]
lowest = dt.nsmallest(12, "trips")
hi = dt.nlargest(5, "trips")
lowmem = da[da.member_casual == "member"].groupby("date").trips.sum()
dst_dates = ["2023-03-12", "2023-11-05", "2024-03-10", "2024-11-03"]
dst_neg = con.execute(f"""
SELECT CAST(started_at AS DATE) AS date, count(*) FILTER (WHERE dur_s <= 0) AS nonpositive,
       count(*) FILTER (WHERE dur_s < -1800) AS lt_neg_30min, count(*) AS rows
FROM f WHERE CAST(started_at AS DATE) IN (DATE '{dst_dates[0]}', DATE '{dst_dates[1]}', DATE '{dst_dates[2]}', DATE '{dst_dates[3]}')
GROUP BY 1 ORDER BY 1
""").df()
neg_tot = con.execute("SELECT count(*) FILTER (WHERE dur_s < 0), count(*) FILTER (WHERE dur_s = 0), count(*) FILTER (WHERE dur_s < -1800) FROM f").fetchone()
neg_by_day = con.execute("SELECT CAST(started_at AS DATE) AS date, count(*) AS n FROM f WHERE dur_s < 0 GROUP BY 1 ORDER BY 2 DESC LIMIT 6").df()
dst_hours = ho[ho.date.isin(pd.to_datetime(dst_dates))].groupby(["date", "hour"]).trips.sum().reset_index()
dst_hour1 = dst_hours[dst_hours.hour.isin([1, 2])].pivot(index="date", columns="hour", values="trips")
hr_ref = ho.groupby("hour").trips.sum() / 731
dst_rows = ho[ho.date.isin(pd.to_datetime(dst_dates))].groupby(["date", "hour"]).trips.sum().unstack()
spill = con.execute("""
SELECT year(started_at)*100+month(started_at) AS start_ym, file_ym, count(*) AS n FROM f
WHERE fm&32=0 AND year(started_at)*100+month(started_at) <> file_ym GROUP BY 1,2 ORDER BY 3 DESC LIMIT 8
""").df()
outside = con.execute("SELECT min(started_at), max(started_at), count(*) FROM f WHERE fm&32>0").fetchone()
dup_files = con.execute("""SELECT count(*) FILTER (WHERE fm&2>0) FROM f""").fetchone()[0]

# per-month clean ratio outliers
pm["clean_pct"] = pm.clean_pct.astype(float)

lines = ["# Anomalies and data-quality notes (auto-generated by 02_clean_aggregate.py)", ""]
lines += [f"- Median clean daily total (both user types): {med:,.0f} trips; 10% threshold = {0.1*med:,.0f}.",
          f"- Days below 10% of the median: {len(low)}" + ("" if len(low) else " (none)"), ""]
if len(low):
    lines += ["| date | trips |", "|---|---|"] + [f"| {r.date.date()} | {r.trips:,} |" for r in low.itertuples()] + [""]
lines += ["## 12 lowest-volume days (clean trips)", "", "| date | dow | trips | % of median |", "|---|---|---|---|"]
for r in lowest.itertuples():
    lines.append(f"| {r.date.date()} | {r.date.strftime('%a')} | {r.trips:,} | {100*r.trips/med:.0f}% |")
lines += ["", "Interpretation (NOT verified against external weather/holiday/outage records here): no day is below 10% of the median, "
          "so nothing is flagged as a suspected outage by the pre-set rule. The lowest days include winter days, Christmas/Thanksgiving "
          "(2023-12-25, 2024-11-28, 2024-12-25) and some spring/autumn weekend days (e.g. 2023-04-29/30, 2024-03-23) that may be "
          "weather-driven; the weather job should check these dates against precipitation before they are described as real.", ""]
lines += ["## 5 highest-volume days", "", "| date | dow | trips |", "|---|---|---|"]
for r in hi.itertuples():
    lines.append(f"| {r.date.date()} | {r.date.strftime('%a')} | {r.trips:,} |")
lines += ["", "## Daylight-saving-time artefacts (timestamps are naive local wall-clock time)", "",
          "- Spring-forward (2023-03-12, 2024-03-10): local hour 02 does not exist, so hour-02 counts are ~0 on those dates and trips spanning the gap look 1 h too long.",
          "- Fall-back (2023-11-05, 2024-11-03): local hour 01 occurs twice and is pooled into one 'hour 1' bin (roughly double); trips that start in the 1st 01:xx and end in the 2nd 01:xx look up to 1 h too short, and some become negative (removed by R3).",
          f"- Rows with negative duration overall: {neg_tot[0]:,}; zero duration: {neg_tot[1]:,}; below -30 min: {neg_tot[2]:,} (see the per-date table below: if all negative rows fall on the two fall-back dates, R3 is a DST artefact, not bad data).",
          "", "Non-positive durations on DST dates:", "", md(dst_neg), "",
          "Top days by number of negative-duration rows:", "", md(neg_by_day), "",
          "Clean trips by local hour on DST dates (hours 0-4):", "", md(dst_rows[[c for c in dst_rows.columns if c <= 4]], index=True), "",
          "These affect at most 4 of 731 days and the 1-2 h bins on them; the effect on seasonal/weekly/hourly summaries is negligible, "
          "but day-level hourly bootstrap should consider excluding the 4 DST dates for sensitivity.", "",
          "## Spill-over between source files and calendar months", "",
          f"- Rows failing R6 (start outside 2023-2024): {outside[2]:,} (range of start times: {outside[0]} to {outside[1]}).",
          "- Rows kept whose start month differs from the source-file month (largest first):", "", md(spill), "",
          "Counts in monthly_counts.csv/daily_counts.csv are by actual start timestamp, not by source-file month. "
          "cleaning_per_month.csv reports both views.", "",
          f"- Duplicate ride_id extra copies removed (R2): {dup_files:,}.", "",
          "## Station ids", "",
          "Rows with missing station ids are kept (descriptive stats in bike_type_and_user_mix.csv, views *_station_missing_*).", "",
          "## Checks", "", "```json", json.dumps(checks, indent=2, default=str), "```", ""]
with open(f"{OUT}/anomalies.md", "w") as fh:
    fh.write("\n".join(lines))
with open(f"{OUT}/checks.json", "w") as fh:
    json.dump(checks, fh, indent=2, default=str)
log("anomalies.md + checks.json written; ALL DONE")
