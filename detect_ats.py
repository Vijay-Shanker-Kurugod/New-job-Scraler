"""Grow and maintain the employer list (companies.csv).

1. Promote employers seen in Arbeitsagentur results (data/ba_employers.csv) that have a
   website domain and are not in companies.csv yet.
2. For every row without an `ats`, open the website, find the career page and detect which
   applicant-tracking system it uses (greenhouse/lever/ashby/smartrecruiters/personio/workday),
   else mark it `generic` with the career-page URL, else `none`.

Run weekly (GitHub Actions) or by hand: python detect_ats.py [--limit 300] [--recheck-none]
"""
import argparse
import csv
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from crawler.common import http

ROOT = os.path.dirname(os.path.abspath(__file__))
FIELDS = ["name", "domain", "type", "ats", "ats_id", "careers_url", "source", "status", "checked"]

PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.(eu\.)?lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)|api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)")),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)")),
    ("workday", re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")),
]
OTHER_BOARDS = re.compile(r"https?://(?:[a-z0-9-]+\.softgarden\.io|join\.com/companies/[A-Za-z0-9_-]+|"
                          r"[a-z0-9-]+\.dvinci-hr\.com|[a-z0-9.-]*onapply\.de|[a-z0-9-]+\.rexx-systems\.com|"
                          r"jobs\.b-ite\.com/[A-Za-z0-9_/-]+|[a-z0-9-]+\.career\.softgarden\.de)[^\"'<> ]*")
CAREER_LINK = re.compile(r'href=["\']([^"\']+)["\'][^>]*>([^<]{0,80})', re.I)
CAREER_WORD = re.compile(r"karriere|career|jobs?\b|stellen|stellenangebote|join[- ]us|arbeiten[- ]bei|"
                         r"offene[- ]positionen|vacancies|jobb?örse", re.I)
BAD_ID = {"embed", "js", "api", "v1", "oneclick-ui", "jobs", "search", "de", "en"}
SKIP_DOMAINS = re.compile(r"arbeitsagentur|stepstone|indeed|linkedin|xing|monster|jobware|kimeta|"
                          r"stellenanzeigen|jobvector|google|facebook|personalwerk|softgarden\.io$")


def match_ats(text):
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            g = [x for x in m.groups() if x is not None]
            if kind == "lever":
                ident = ("eu:" if m.group(1) else "") + m.group(2)
            elif kind == "workday":
                ident = "|".join(m.groups())
            else:
                ident = g[0]
            if ident.split(":")[-1].split("|")[0].lower() not in BAD_ID:
                return kind, ident
    return None, None


SF_MARK = re.compile(r"successfactors\.(?:eu|com)/verp|career_company=|rmk-map|jobs2web", re.I)
EF_MARK = re.compile(r"eightfold\.ai|/api/pcsx/", re.I)
DV_MARK = re.compile(r"dvinci(?:-hr)?\.com|/jobPublication/", re.I)
JOBLIST_WORD = re.compile(r"stellenangebot|stellenb?örse|offene[- ]stellen|jobsuche|job[- ]?search|search[- ]?jobs|"
                          r"alle[- ]jobs|all[- ]jobs|jobs?finder|job[- ]?offers|vacanc|/jobs/?$|/search", re.I)
BAD_CAREER = re.compile(r"diversit|faq|contact|kontakt|fraud|privacy|datenschutz|benefit|why-|warum|"
                        r"esports|pupil|schueler|schüler|initiativ|candidate-privacy|talent-?pool", re.I)


def extra_match(txt, url, row):
    """Career systems identified by fingerprints rather than a board URL."""
    host = urlparse(url).netloc
    if SF_MARK.search(txt) and host:
        return "successfactors", host
    if EF_MARK.search(txt) and host:
        dom = (row.get("domain") or host.split(".", 1)[-1]).lower()
        return "eightfold", f"{host}|{dom}"
    if DV_MARK.search(txt) and host and "dvinci" not in host:
        return "dvinci", host
    return None, None


def detect(row):
    pages = []
    if row.get("careers_url"):
        pages.append(row["careers_url"])
    dom = row.get("domain")
    if dom:
        pages.append(f"https://{dom}")
    visited, career_page = set(), ""
    i = 0
    while i < len(pages) and len(visited) < 6:
        url = pages[i]; i += 1
        if url in visited:
            continue
        visited.add(url)
        r = http("GET", url, retries=1, timeout=20)
        if r is None or r.status_code >= 400:
            continue
        txt = r.text
        kind, ident = match_ats(txt + " " + r.url)
        if kind:
            return kind, ident, row.get("careers_url") or r.url
        kind, ident = extra_match(txt, r.url, row)
        if kind:
            return kind, ident, r.url
        other = OTHER_BOARDS.search(txt)
        if other:
            return "generic", "", other.group(0)
        # collect career links from the homepage
        for href, label in CAREER_LINK.findall(txt):
            if (CAREER_WORD.search(href) or CAREER_WORD.search(label)) and not BAD_CAREER.search(href):
                u = urljoin(r.url, href)
                if urlparse(u).scheme.startswith("http") and u not in visited and len(pages) < 10:
                    # job lists first, generic "Karriere" pages after them
                    if JOBLIST_WORD.search(href + " " + label):
                        pages.insert(i, u)
                        career_page = u if not JOBLIST_WORD.search(career_page or "") else career_page
                    else:
                        pages.append(u)
                        career_page = career_page or u
        if url != row.get("careers_url") and CAREER_WORD.search(url):
            career_page = url
    if not career_page and dom:
        for path in ("/karriere", "/de/karriere", "/careers", "/jobs"):
            r = http("GET", f"https://{dom}{path}", retries=0, timeout=15)
            if r is not None and r.status_code == 200:
                kind, ident = match_ats(r.text)
                if kind:
                    return kind, ident, r.url
                career_page = r.url
                break
    return ("generic", "", career_page) if career_page else ("none", "", "")


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400, help="max companies to probe this run")
    ap.add_argument("--recheck-none", action="store_true")
    ap.add_argument("--recheck-generic", action="store_true",
                    help="re-probe rows marked generic (finds SuccessFactors/Eightfold/d.vinci and better job-list URLs)")
    a = ap.parse_args()
    path = os.path.join(ROOT, "companies.csv")
    rows = load(path)
    names = {r["name"].lower() for r in rows}
    doms = {r["domain"].lower() for r in rows if r.get("domain")}

    added = 0
    for e in load(os.path.join(ROOT, "data", "ba_employers.csv")):
        d = (e.get("domain") or "").lower()
        if d and not SKIP_DOMAINS.search(d) and d not in doms and e["name"].lower() not in names:
            rows.append({"name": e["name"], "domain": d, "type": "", "ats": "", "ats_id": "", "careers_url": "",
                         "source": "Arbeitsagentur", "status": "active", "checked": ""})
            doms.add(d); names.add(e["name"].lower()); added += 1

    todo = [r for r in rows if not r.get("ats") or (a.recheck_none and r["ats"] == "none")
            or (a.recheck_generic and r["ats"] == "generic" and "manual" not in (r.get("source") or ""))][: a.limit]
    print(f"promoted {added} employers from Arbeitsagentur; probing {len(todo)} sites")

    def work(r):
        try:
            return r, detect(r)
        except Exception as ex:  # noqa: BLE001
            return r, ("none", "", f"error {type(ex).__name__}")
    from crawler.common import TODAY
    with ThreadPoolExecutor(max_workers=10) as ex:
        for r, (kind, ident, url) in ex.map(work, todo):
            r["ats"], r["ats_id"] = kind, ident
            if url and not url.startswith("error"):
                r["careers_url"] = url
            r["checked"] = TODAY.isoformat()
            print(f"  {r['name'][:40]:40} {kind:15} {ident or url[:60]}")

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            r.setdefault("status", "active")
            w.writerow({k: r.get(k, "") for k in FIELDS})
    counts = {}
    for r in rows:
        counts[r.get("ats") or "unchecked"] = counts.get(r.get("ats") or "unchecked", 0) + 1
    print("employer list:", len(rows), counts)


if __name__ == "__main__":
    main()
