"""Full sweep: list EVERY currently open, relevant job on every employer's career page.

Unlike crawl.py (daily, only jobs never seen before), this re-reads every career page and
reports every open relevant job that is at most --max-age days old (default 7):
dated jobs by their publish date; undated jobs only if the site was crawled before and the
job was not on it then (so it is provably new). Undated jobs that cannot be dated are dropped
and counted in stats["undated_dropped"]. Writes data/sweep/<date>.json ({"items": [...], "stats": {...}}) in the same
field names score.py / extract.py read, plus data/sweep/<date>-companies.json (what each
employer's site returned, for the coverage report).

Usage: python sweep.py [--ba-days 60] [--workers 10] [--per-site 40] [--only NAME]
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from crawler import ats, ba, generic
from crawler.common import TODAY, age_days, load_json, relevant, save_json
from crawl import P, browser_pass, load_companies, merge_employers


def sweep_company(row, per_site):
    kind = (row.get("ats") or "").strip()
    t = time.time()
    try:
        if kind in ats.ADAPTERS and row.get("ats_id"):
            jobs = ats.ADAPTERS[kind](row)
            return row, jobs, None, {"method": kind, "candidates": len(jobs), "secs": round(time.time() - t)}
        if row.get("careers_url"):
            cands = generic.listing(row)
            out, fetched = [], 0
            for c in cands:
                if not c["jobDescription"]:
                    if fetched >= per_site:
                        c["jobDescription"] = ""
                        out.append(c)          # keep title-only hit; score.py reads the title
                        continue
                    generic.enrich(c); fetched += 1; time.sleep(0.25)
                if c["jobDescription"] and not relevant(c["jobTitle"], c["jobDescription"]):
                    continue
                out.append(c)
            return row, out, None, {"method": "generic", "candidates": len(cands), "secs": round(time.time() - t)}
        return row, [], "no career page found", {"method": kind or "unchecked", "candidates": 0}
    except Exception as e:  # one broken site must not stop the run
        return row, [], f"{type(e).__name__}: {e}"[:200], {"method": kind, "candidates": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age", type=int, default=7)
    ap.add_argument("--ba-days", type=int, default=None)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--per-site", type=int, default=40)
    ap.add_argument("--no-ba", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args()
    ats.MAX_AGE = a.max_age
    a.ba_days = a.ba_days or a.max_age
    state = load_json(P("state", "seen.json"), {"jobs": {}, "companies": {}})
    os.makedirs(P("data", "sweep"), exist_ok=True)
    cfg = load_json(P("config.json"), {})
    today = TODAY.isoformat()
    items, report = [], []
    stats = {"date": today, "ba": 0, "companies": 0, "companies_with_jobs": 0, "errors": 0}

    if not a.no_ba:
        t = time.time()
        jobs, employers = ba.crawl(cfg.get("ba_keywords", []), days=a.ba_days)
        merge_employers(employers)
        for j in jobs:
            j["dateSource"] = "posted"
        items += jobs
        stats["ba"] = len(jobs)
        print(f"BA ({a.ba_days} days): {len(jobs)} relevant, {len(employers)} employers, {time.time()-t:.0f}s", flush=True)

    rows = [r for r in load_companies() if r.get("ats") != "none"]
    if a.only:
        rows = [r for r in rows if a.only.lower() in r["name"].lower()]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(sweep_company, r, a.per_site) for r in rows]
        for f in as_completed(futs):
            row, jobs, err, info = f.result()
            stats["companies"] += 1
            stats["errors"] += bool(err)
            stats["companies_with_jobs"] += bool(jobs)
            for j in jobs:
                j["dateSource"] = "posted" if j.get("publishedAt") else "no_date"
            items += jobs
            report.append({"name": row["name"], "domain": row.get("domain"), "type": row.get("type"),
                           "careers_url": row.get("careers_url"), "jobs": len(jobs), "error": err, **info})
            print(f"  {row['name'][:40]:40} {info.get('method',''):15} {len(jobs):3} {err or ''}", flush=True)

    if not a.no_browser:
        empty = [r for r in rows if r.get("careers_url") and (r.get("ats") or "generic") == "generic"
                 and not any(x["name"] == r["name"] and x["jobs"] for x in report)]
        t = time.time()
        for row, jobs, err in browser_pass(empty, {}, per_site=a.per_site):
            for j in jobs:
                j["dateSource"] = "posted" if j.get("publishedAt") else "no_date"
                j["companyName_browser"] = row["name"] + "|browser"
            items += jobs
            for rep in report:
                if rep["name"] == row["name"]:
                    rep.update(browser_jobs=len(jobs), browser_error=err)
        stats["browser_sites"] = len(empty)
        print(f"browser pass: {len(empty)} sites, {time.time()-t:.0f}s", flush=True)

    seen, uniq = set(), []
    stats.update(too_old=0, undated_dropped=0)
    for j in items:
        if j["jobId"] in seen:
            continue
        seen.add(j["jobId"])
        if j.get("publishedAt"):
            if (age_days(j["publishedAt"]) or 0) > a.max_age:
                stats["too_old"] += 1; continue
        else:
            first = state["companies"].get(j.get("companyName_browser") or j["companyName"])
            if not first or first >= today or j["jobId"] in state["jobs"]:
                stats["undated_dropped"] += 1; continue
            j["publishedAt"], j["dateSource"] = today, "first_seen"
        uniq.append(j)
    stats["jobs"] = len(uniq)
    save_json(P("data", "sweep", f"{today}.json"), {"items": uniq, "stats": stats})
    save_json(P("data", "sweep", f"{today}-companies.json"), sorted(report, key=lambda r: r["name"].lower()))
    print("SWEEP", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
