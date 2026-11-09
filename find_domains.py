"""Find the website of Arbeitsagentur employers that did not list one.

Looks each employer name up on DuckDuckGo's HTML search and takes the first result that is
not a job board, social network or directory. Staffing agencies are skipped (their jobs
reach us through the Arbeitsagentur anyway). Writes the domain into data/ba_employers.csv;
detect_ats.py then promotes those employers into companies.csv.

Usage: python find_domains.py [--limit 400]
"""
import argparse
import csv
import os
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

from crawler.common import http

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "data", "ba_employers.csv")
AGENCY = re.compile(r"ferchau|brunel|akkodis|orange engineering|engineering people|\bep\b|hays|randstad|adecco|"
                    r"manpower|orizon|gulp|amadeus fire|\bdis\b|yer\b|persona|tempton|zeitarbeit|personal|"
                    r"kirsch|hofmann|workwise|jobster|page|kummer|recruit|staffing|consult|vermittlung|"
                    r"arbeitnehmerüberlassung|projekt ?partner|talent|career|job", re.I)
BAD_HOST = re.compile(r"duckduckgo|arbeitsagentur|stepstone|indeed|linkedin|xing|kununu|glassdoor|monster|jobware|"
                      r"kimeta|stellenanzeigen|jobvector|meinestadt|wikipedia|facebook|instagram|youtube|"
                      r"northdata|firmenwissen|unternehmensregister|dasoertliche|gelbeseiten|wlw\.de|"
                      r"europages|bloomberg|crunchbase|zoominfo|dnb\.com|companyhouse|online-handelsregister|"
                      r"stellenmarkt|jobs\.|jooble|careerjet|absolventa|azubiyo|ausbildung|bewerbung", re.I)
CLEAN = re.compile(r"\b(gmbh|mbh|ag|se|kg|co|kgaa|ug|e\.v\.|& co\.?|haftungsbeschränkt|niederlassung|nl|"
                   r"zweigniederlassung|standort)\b.*$", re.I)


def lookup(name):
    q = CLEAN.sub("", name).strip(" ,-&") or name
    r = http("POST", "https://html.duckduckgo.com/html/", data={"q": f"{q} Karriere", "kl": "de-de"},
             retries=1, timeout=20)
    if r is None or r.status_code != 200:
        return None, f"http {getattr(r, 'status_code', 'none')}"
    for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', r.text):
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
        host = urlparse(href).netloc.lower().removeprefix("www.")
        if host and not BAD_HOST.search(host):
            return host, ""
    return None, "no result"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    a = ap.parse_args()
    with open(PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    todo = [r for r in rows if not r.get("domain") and not AGENCY.search(r["name"])][: a.limit]
    found = 0
    for i, r in enumerate(todo):
        dom, err = lookup(r["name"])
        if dom:
            r["domain"] = dom; found += 1
        print(f"  {r['name'][:50]:50} {dom or err}", flush=True)
        if err.startswith("http 4") or err.startswith("http 202"):
            print("search engine is refusing requests – stopping early"); break
        time.sleep(2.5)
    with open(PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "domain", "hits", "first_seen", "last_seen"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"looked up {len(todo)}, found {found}")


if __name__ == "__main__":
    main()
