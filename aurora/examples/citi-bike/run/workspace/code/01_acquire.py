#!/usr/bin/env python3
"""Download, extract and convert Citi Bike 2023-2024 trip data to parquet; write manifest.

Usage: python3 -I /app/code/01_acquire.py [download|extract|convert|manifest|all]
Resumable: finished downloads/extractions/parquets are skipped.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import duckdb

APP = "/app"
BASE = "https://s3.amazonaws.com/tripdata/"
URLS = {
    2023: ["2023-citibike-tripdata.zip"],
    2024: [f"2024{m:02d}-citibike-tripdata.zip" for m in range(1, 13)],
}
RAW = {y: f"{APP}/raw_{y}" for y in URLS}
EXT = {y: f"{APP}/extract_{y}" for y in URLS}
PQ = {y: f"{APP}/parquet/trips_{y}.parquet" for y in URLS}
MANIFEST = f"{APP}/output/tables/data_manifest.json"
PARTIAL = f"{APP}/output/tables/_manifest_partial.json"
UA = {"User-Agent": "Mozilla/5.0 (research; citibike analysis)"}


def head_size(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def download_one(name, year, retries=6):
    url = BASE + name
    dest = os.path.join(RAW[year], name)
    try:
        size = head_size(url)
    except Exception as e:
        return name, None, f"HEAD failed: {e!r}"
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        return name, size, "cached"
    part = dest + ".part"
    err = None
    for attempt in range(1, retries + 1):
        try:
            have = os.path.getsize(part) if os.path.exists(part) else 0
            hdr = dict(UA)
            if have:
                hdr["Range"] = f"bytes={have}-"
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=120) as r:
                mode = "ab" if (have and r.status == 206) else "wb"
                with open(part, mode) as f:
                    shutil.copyfileobj(r, f, 1 << 20)
            if os.path.getsize(part) == size:
                os.replace(part, dest)
                return name, size, "downloaded"
            err = f"size mismatch {os.path.getsize(part)} != {size}"
        except Exception as e:
            err = repr(e)
        time.sleep(5 * attempt)
    return name, size, f"FAILED after {retries} tries: {err}"


def stage_download():
    jobs = [(n, y) for y, ns in URLS.items() for n in ns]
    status = {}
    with ThreadPoolExecutor(4) as ex:
        for name, size, st in ex.map(lambda a: download_one(*a), jobs):
            print(name, size, st, flush=True)
            status[name] = {"expected_size": size, "status": st}
    json.dump(status, open(PARTIAL, "w"), indent=1)
    return status


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


def extract_zip(zpath, outdir, prefix=""):
    """Recursively extract CSVs (and nested zips) into outdir; returns list of csv paths."""
    csvs = []
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            n = info.filename
            if info.is_dir() or n.startswith("__MACOSX") or "/__MACOSX/" in n or os.path.basename(n).startswith("._"):
                continue
            base = safe_name(prefix + n.replace("/", "__"))
            low = n.lower()
            if low.endswith(".csv"):
                dest = os.path.join(outdir, base)
                if not (os.path.exists(dest) and os.path.getsize(dest) == info.file_size):
                    with z.open(info) as src, open(dest + ".tmp", "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    os.replace(dest + ".tmp", dest)
                csvs.append(dest)
            elif low.endswith(".zip"):
                nested = os.path.join(outdir, base)
                if not (os.path.exists(nested) and os.path.getsize(nested) == info.file_size):
                    with z.open(info) as src, open(nested + ".tmp", "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    os.replace(nested + ".tmp", nested)
                csvs += extract_zip(nested, outdir, prefix=base + "__")
                os.remove(nested)
            else:
                print("  ignoring non-csv entry:", n)
    return csvs


def stage_extract():
    for y in URLS:
        os.makedirs(EXT[y], exist_ok=True)
        for n in URLS[y]:
            zp = os.path.join(RAW[y], n)
            if not os.path.exists(zp):
                print("MISSING zip", zp)
                continue
            with zipfile.ZipFile(zp) as z:
                print(n, "listing:")
                for i in z.infolist()[:60]:
                    print("   ", i.filename, i.file_size)
            csvs = extract_zip(zp, EXT[y], prefix=f"{n[:-4]}__")
            print(n, "->", len(csvs), "csv parts")


def list_csvs(y):
    if not os.path.isdir(EXT[y]):
        return []
    return sorted(os.path.join(EXT[y], f) for f in os.listdir(EXT[y]) if f.lower().endswith(".csv"))


EXPECTED = ["ride_id", "rideable_type", "started_at", "ended_at", "start_station_name", "start_station_id",
            "end_station_name", "end_station_id", "start_lat", "start_lng", "end_lat", "end_lng", "member_casual"]


def stage_convert():
    con = duckdb.connect()
    con.execute("PRAGMA threads=32")
    for y in URLS:
        files = list_csvs(y)
        if not files:
            print("no csvs for", y)
            continue
        if os.path.exists(PQ[y]):
            print("exists", PQ[y])
            continue
        flist = ",".join("'" + f.replace("'", "''") + "'" for f in files)
        src = (f"read_csv([{flist}], all_varchar=true, union_by_name=true, header=true, filename=true, "
               f"sample_size=-1, ignore_errors=false, null_padding=true)")
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
        print(y, "columns:", cols)
        missing = [c for c in EXPECTED if c not in cols]
        if missing:
            print("WARNING missing expected columns", missing)
        sel = []
        for c in cols:
            if c == "filename":
                sel.append("regexp_extract(filename, '[^/]+$') AS source_file")
            elif c in ("started_at", "ended_at"):
                sel.append(f"{c} AS {c}_raw")
                sel.append(f"TRY_CAST({c} AS TIMESTAMP) AS {c}")
            elif c in ("start_lat", "start_lng", "end_lat", "end_lng"):
                sel.append(f"TRY_CAST({c} AS DOUBLE) AS {c}")
            else:
                sel.append(f'"{c}"')
        tmp = PQ[y] + ".tmp"
        con.execute(f"COPY (SELECT {', '.join(sel)} FROM {src}) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)")
        os.replace(tmp, PQ[y])
        print("wrote", PQ[y], os.path.getsize(PQ[y]))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def stage_manifest():
    con = duckdb.connect()
    con.execute("PRAGMA threads=32")
    status = json.load(open(PARTIAL)) if os.path.exists(PARTIAL) else {}
    man = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "base_url": BASE, "downloads": [], "years": {}, "flags": []}
    for y, ns in URLS.items():
        for n in ns:
            p = os.path.join(RAW[y], n)
            d = {"url": BASE + n, "year_group": y, "file": n,
                 "download_status": status.get(n, {}).get("status")}
            if os.path.exists(p):
                d["size_bytes"] = os.path.getsize(p)
                d["sha256"] = sha256(p)
                with zipfile.ZipFile(p) as z:
                    d["zip_members"] = [{"name": i.filename, "size": i.file_size} for i in z.infolist()
                                        if not i.is_dir() and "__MACOSX" not in i.filename]
            else:
                d["size_bytes"] = None
                man["flags"].append(f"MISSING download {n}")
            man["downloads"].append(d)

    for y in URLS:
        files = list_csvs(y)
        yi = {"csv_parts": [], "parquet": PQ[y]}
        csv_total = 0
        for f in files:
            flit = f.replace("'", "''")
            rows = con.execute(f"SELECT count(*) FROM read_csv('{flit}', all_varchar=true, header=true, null_padding=true)").fetchone()[0]
            hdr = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_csv('{flit}', all_varchar=true, header=true, sample_size=1000)").fetchall()]
            yi["csv_parts"].append({"file": os.path.basename(f), "size_bytes": os.path.getsize(f), "rows": rows, "columns": hdr})
            csv_total += rows
        yi["csv_total_rows"] = csv_total
        if os.path.exists(PQ[y]):
            q = f"'{PQ[y]}'"
            yi["parquet_rows"] = con.execute(f"SELECT count(*) FROM {q}").fetchone()[0]
            yi["parquet_size_bytes"] = os.path.getsize(PQ[y])
            yi["schema"] = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM {q}").fetchall()}
            yi["rows_per_source_file_parquet"] = {r[0]: r[1] for r in con.execute(
                f"SELECT source_file, count(*) FROM {q} GROUP BY 1 ORDER BY 1").fetchall()}
            r = con.execute(f"SELECT min(started_at), max(started_at), min(ended_at), max(ended_at), "
                            f"count(*) FILTER (WHERE started_at IS NULL), count(*) FILTER (WHERE ended_at IS NULL), "
                            f"count(*) FILTER (WHERE started_at_raw IS NOT NULL AND started_at IS NULL), "
                            f"count(*) FILTER (WHERE ended_at_raw IS NOT NULL AND ended_at IS NULL) FROM {q}").fetchone()
            yi["started_at_min"], yi["started_at_max"], yi["ended_at_min"], yi["ended_at_max"] = [str(v) for v in r[:4]]
            yi["null_started_at"], yi["null_ended_at"], yi["unparseable_started_at"], yi["unparseable_ended_at"] = r[4:]
            yi["rows_per_start_month"] = {str(r[0])[:7]: r[1] for r in con.execute(
                f"SELECT date_trunc('month', started_at) m, count(*) FROM {q} GROUP BY 1 ORDER BY 1").fetchall()}
            yi["started_at_raw_samples"] = [r[0] for r in con.execute(f"SELECT DISTINCT started_at_raw FROM {q} USING SAMPLE 5").fetchall()]
            yi["member_casual_values"] = {str(r[0]): r[1] for r in con.execute(
                f"SELECT member_casual, count(*) FROM {q} GROUP BY 1 ORDER BY 2 DESC").fetchall()}
            yi["rideable_type_values"] = {str(r[0]): r[1] for r in con.execute(
                f"SELECT rideable_type, count(*) FROM {q} GROUP BY 1 ORDER BY 2 DESC").fetchall()}
            if yi["parquet_rows"] != csv_total:
                man["flags"].append(f"{y}: parquet rows {yi['parquet_rows']} != csv rows {csv_total}")
            # month checks
            for m in range(1, 13):
                key = f"{y}-{m:02d}"
                n = yi["rows_per_start_month"].get(key, 0)
                if n < 500_000 or n > 6_000_000:
                    man["flags"].append(f"{key}: row count {n} outside expected range [0.5M, 6M]")
        else:
            man["flags"].append(f"{y}: parquet missing")
        man["years"][y] = yi
    json.dump(man, open(MANIFEST, "w"), indent=1, default=str)
    print("manifest written; flags:", man["flags"])
    miss = [f for f in man["flags"] if f.startswith("MISSING") or "parquet missing" in f]
    if miss:
        with open(f"{APP}/output/DATA_UNAVAILABLE.md", "w") as f:
            f.write("# Data unavailable\n\nThe following could not be obtained; no data was fabricated.\n\n")
            f.writelines(f"- {m}\n" for m in miss)


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    for y in URLS:
        os.makedirs(RAW[y], exist_ok=True)
    if stage in ("download", "all"):
        stage_download()
    if stage in ("extract", "all"):
        stage_extract()
    if stage in ("convert", "all"):
        stage_convert()
    if stage in ("manifest", "all"):
        stage_manifest()
