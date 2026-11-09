"""Daily crawl: Arbeitsagentur API + every employer in companies.csv.

Writes data/new/<date>.json ({"items": [...]}, same field names as the LinkedIn scraper, so
score.py / extract.py work unchanged) and data/latest.json. Only jobs never seen before are
written. Dated jobs older than MAX_AGE days are recorded as seen and skipped.
Undated jobs (custom career pages) get publishedAt = first-seen date and dateSource = "first_seen";
on the very first crawl of a company they are only recorded (baseline), not reported.
"""
import argparse
import re
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from crawler import ats, ba, boards, generic, jobspipe, score
from crawler.common import TODAY, contacts, age_days, load_json, relevant, save_json

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)


def due_today(row, day=None):
    """Tier A (relevant, hand lists, research) every day; tier B once a week by a stable hash."""
    if (row.get("tier") or "A") != "B":
        return True
    import hashlib
    day = TODAY.toordinal() if day is None else day
    return int(hashlib.md5(row["name"].encode()).hexdigest(), 16) % 7 == day % 7


def load_companies(all_rows=False):
    with open(P("companies.csv"), encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("status") or "active") == "active"
                and (r.get("ats") or "") != "none" and (r.get("ats") or r.get("careers_url"))]
    return rows if all_rows else [r for r in rows if due_today(r)]


DEADLINE = [0.0]   # epoch seconds; companies not started by then are left for the next run


def crawl_company(row, seen_jobs):
    kind = (row.get("ats") or "").strip()
    if DEADLINE[0] and time.time() > DEADLINE[0]:
        return row, [], "skipped: time budget"
    try:
        if kind in ats.ADAPTERS and row.get("ats_id"):
            return row, ats.ADAPTERS[kind](row), None
        if row.get("careers_url"):
            cands = generic.listing(row)
            out = []
            fetched = 0
            for c in cands:
                if c["jobId"] in seen_jobs:
                    out.append(c); continue          # known: no need to re-fetch
                if not c["jobDescription"] and fetched < 15:
                    generic.enrich(c); fetched += 1; time.sleep(0.3)
                if c["jobDescription"] and not relevant(c["jobTitle"], c["jobDescription"]):
                    continue
                out.append(c)
            return row, out, None
        return row, [], "no ats/careers_url"
    except Exception as e:  # one broken site must not stop the run
        return row, [], f"{type(e).__name__}: {e}"[:200]


def browser_pass(rows, seen_jobs, per_site=15, log=print, procs=6):
    """Second pass for generic career pages that showed no jobs in plain HTML: render them in
    headless Chromium (JavaScript-built job lists), `procs` browsers in parallel (keeps the
    GitHub Actions minutes low). Returns [(row, jobs, err)]."""
    from concurrent.futures import ProcessPoolExecutor
    if not rows:
        return []
    chunks = [rows[i::procs] for i in range(procs) if rows[i::procs]]
    res = []
    with ProcessPoolExecutor(max_workers=len(chunks)) as ex:
        for part in ex.map(_browser_chunk, chunks, [seen_jobs] * len(chunks), [per_site] * len(chunks)):
            res += part
    return res


def _browser_chunk(rows, seen_jobs, per_site=15, log=print):
    try:
        from crawler.browser import Browser
        br = Browser()
    except Exception as e:  # noqa: BLE001
        log(f"browser pass skipped: {type(e).__name__}: {e}"[:200]); return []
    res = []
    try:
        for row in rows:
            t = time.time()
            try:
                cands, ids = [], set()
                for u, html in br.render(row["careers_url"]):
                    for c in generic.parse(row, html, u):
                        if c["jobId"] not in ids:
                            ids.add(c["jobId"]); cands.append(c)
                out, fetched = [], 0
                for c in cands:
                    if c["jobId"] in seen_jobs:
                        out.append(c); continue
                    if not c["jobDescription"] and fetched < per_site:
                        generic.enrich(c); fetched += 1; time.sleep(0.25)
                    if c["jobDescription"] and not relevant(c["jobTitle"], c["jobDescription"]):
                        continue
                    for j in [c]:
                        j["source"] = j["source"].replace("Career site (", "Career site (browser) (", 1)
                    out.append(c)
                res.append((row, out, None))
                log(f"  [browser] {row['name'][:38]:38} {len(cands):3} links {len(out):3} kept {time.time()-t:.0f}s")
            except Exception as e:  # noqa: BLE001
                res.append((row, [], f"browser {type(e).__name__}: {e}"[:200]))
    finally:
        br.close()
    return res


def merge_employers(employers):
    path = P("data", "ba_employers.csv")
    rows = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["name"]] = r
    for name, dom in employers.items():
        if not name:
            continue
        r = rows.setdefault(name, {"name": name, "domain": dom, "hits": "0", "first_seen": TODAY.isoformat()})
        r["hits"] = str(int(r["hits"]) + 1)
        if dom and not r["domain"]:
            r["domain"] = dom
        r["last_seen"] = TODAY.isoformat()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "domain", "hits", "first_seen", "last_seen"])
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: -int(r["hits"])):
            w.writerow({k: r.get(k, "") for k in w.fieldnames})


def health_check(stats, days=7):
    """Warn when a source that normally delivers jobs delivers none, or the run ran out of time."""
    import glob
    hist = {}
    files = sorted(glob.glob(P("data", "new", "*.json")) + glob.glob(P("data", "processed", "*.json")))
    for fpath in files[-days:]:
        st = load_json(fpath, {}).get("stats") or {}
        for src, n in (st.get("per_source") or {}).items():
            hist.setdefault(src, []).append(n)
    warn = []
    for src, ns in hist.items():
        avg = sum(ns) / max(len(ns), 1)
        if avg >= 3 and stats["per_source"].get(src, 0) == 0:
            warn.append(f"{src}: 0 new jobs today (7-day average {avg:.0f}) – source may be broken")
    if stats.get("skipped_time_budget"):
        warn.append(f"{stats['skipped_time_budget']} career pages skipped for time; they go first next run")
    errs = len(stats.get("errors") or {})
    if stats.get("companies") and errs / stats["companies"] > 0.25:
        warn.append(f"{errs} of {stats['companies']} career pages failed today")
    return warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age", type=int, default=7)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-ba", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-boards", action="store_true")
    ap.add_argument("--only-boards", action="store_true")
    ap.add_argument("--only", help="crawl only companies whose name contains this")
    ap.add_argument("--budget-min", type=int, default=260,
                    help="stop starting new career pages after this many minutes (rest goes first next run)")
    ap.add_argument("--first-crawl-age", type=int, default=30,
                    help="on a company's first crawl, report dated jobs up to this age instead of --max-age")
    a = ap.parse_args()
    os.makedirs(P("data", "new"), exist_ok=True)
    os.makedirs(P("state"), exist_ok=True)
    cfg = load_json(P("config.json"), {})
    state = load_json(P("state", "seen.json"), {"jobs": {}, "companies": {}})
    seen_jobs, seen_cos = state["jobs"], state["companies"]
    seen_keys = state.setdefault("keys", {})
    today = TODAY.isoformat()
    stats = {"date": today, "ba": 0, "companies": 0, "errors": {}, "per_source": {}}
    collected = []
    started = time.time()
    DEADLINE[0] = started + a.budget_min * 60

    if not a.no_ba:
        t = time.time()
        jobs, employers = ba.crawl(cfg.get("ba_keywords", []), days=a.max_age)
        merge_employers(employers)
        collected += [(j, None) for j in jobs]
        stats["ba"] = len(jobs)
        print(f"BA: {len(jobs)} relevant, {len(employers)} employers, {time.time()-t:.0f}s")

    if not a.no_boards:
        t = time.time()
        bjobs, bstats = boards.crawl_all(cfg, seen_jobs)
        collected += [(j, None) for j in bjobs]
        stats["boards"] = bstats
        print(f"boards: {len(bjobs)} listed, {bstats}, {time.time()-t:.0f}s")

    if not a.no_boards:
        jjobs, jstats = jobspipe.crawl()
        collected += [(j, None) for j in jjobs]
        stats.setdefault("boards", {}).update(jstats)

    rows = [] if a.only_boards else load_companies()
    if a.only:
        rows = [r for r in rows if a.only.lower() in r["name"].lower()]
    # tier A first, then companies skipped last time for lack of time, then the rest
    skipped_before = set(state.get("skipped", []))
    rows.sort(key=lambda r: ((r.get("tier") or "A") != "A", r["name"] not in skipped_before))
    skipped_now = []
    empty_generic = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(crawl_company, r, seen_jobs) for r in rows]
        for f in as_completed(futs):
            row, jobs, err = f.result()
            if err == "skipped: time budget":
                skipped_now.append(row["name"]); continue
            stats["companies"] += 1
            if err:
                stats["errors"][row["name"]] = err
            collected += [(j, row) for j in jobs]
            if not jobs and row.get("careers_url") and (row.get("ats") or "generic") == "generic":
                empty_generic.append(row)
    state["skipped"] = skipped_now
    stats["skipped_time_budget"] = len(skipped_now)
    if not a.no_browser and empty_generic and time.time() < DEADLINE[0] + 20 * 60:
        t = time.time()
        for row, jobs, err in browser_pass(empty_generic, seen_jobs):
            brow = dict(row, name=row["name"] + "|browser")      # separate baseline for browser-read sites
            collected += [(j, brow) for j in jobs]
            if err:
                stats["errors"][brow["name"]] = err
            else:
                rows.append(brow)
        stats["browser_sites"] = len(empty_generic)
        print(f"browser pass: {len(empty_generic)} sites, {time.time()-t:.0f}s")

    new, baseline, old, dupes = [], 0, 0, 0
    norm = lambda x: re.sub(r"\(?[mwfdx]/[mwfdx](/[mwfdx])?\)?|\(all genders?\)|[^a-z0-9äöüß]+", "",
                            (x or "").lower())
    for j, row in collected:
        jid = j["jobId"]
        if jid in seen_jobs:
            continue
        key = norm(j["jobTitle"])[:60] + "|" + norm(j["companyName"])[:15]
        src0 = j["source"].split(" (")[0]
        if seen_keys.get(key, src0) != src0:       # same job already reported via another source
            seen_jobs[jid] = today
            dupes += 1
            continue
        seen_keys.setdefault(key, src0)
        first_crawl = row is not None and row["name"] not in seen_cos
        if j["publishedAt"]:
            seen_jobs[jid] = today
            if (age_days(j["publishedAt"]) or 0) > (a.first_crawl_age if first_crawl else a.max_age):
                old += 1; continue
            j["dateSource"] = "posted"
        else:
            seen_jobs[jid] = today
            if first_crawl and (row.get("tier") or "A") != "A":
                baseline += 1; continue            # first crawl of a weekly (tier B) site: just remember
            j["publishedAt"] = today
            # first crawl of a tier-A site: its open jobs are reported once, flagged, so none are missed
            j["dateSource"] = "first_crawl" if first_crawl else "first_seen"
        if "applyMethod" not in j:
            j.update(contacts(j.get("jobDescription")))
        j.update(score.prescore(j))
        new.append(j)
        src = j["source"].split(" (")[0]
        stats["per_source"][src] = stats["per_source"].get(src, 0) + 1
    for r in rows:   # only sites that answered count as "baselined"
        if r["name"] not in stats["errors"]:
            seen_cos.setdefault(r["name"], today)

    new.sort(key=lambda j: -j.get("preScore", 0))
    stats.update(new=len(new), baseline_recorded=baseline, too_old=old, cross_source_dupes=dupes,
                 minutes=round((time.time() - started) / 60), health=health_check(stats))
    day_file = P("data", "new", f"{today}.json")
    prev = load_json(day_file, {"items": []})          # a 2nd run on the same day appends
    have = {j["jobId"] for j in prev["items"]}
    out = {"items": prev["items"] + [j for j in new if j["jobId"] not in have], "stats": stats}
    save_json(day_file, out)
    save_json(P("data", "latest.json"), out)
    save_json(P("state", "seen.json"), state)
    print(f"NEW {len(new)} | baseline {baseline} | too old {old} | companies {stats['companies']} "
          f"| errors {len(stats['errors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
