"""Add every German university, Hochschule and public research institute to companies.csv.

Source: Wikidata (free SPARQL endpoint). Picks items located in Germany that are an instance of
(a subclass of) university, higher-education institution or research institute and have an official
website. New rows get type "University / research" and an empty `ats`, so detect_ats.py then finds
each career page. Existing rows (same domain or name) are left alone.

Run: python add_research.py   (GitHub Actions; the Claude workspace cannot reach Wikidata)
"""
import csv
import os
import re
from urllib.parse import urlparse

from crawler.common import http

ROOT = os.path.dirname(os.path.abspath(__file__))
FIELDS = ["name", "domain", "type", "ats", "ats_id", "careers_url", "source", "status", "checked"]
QUERY = """
SELECT DISTINCT ?item ?itemLabel ?site WHERE {
  VALUES ?cls { wd:Q3918 wd:Q38723 wd:Q31855 wd:Q1371037 wd:Q875538 wd:Q902104 wd:Q15936437
                wd:Q1663017 wd:Q1664720 wd:Q2385804 wd:Q7315155 wd:Q1438053 wd:Q23002054 wd:Q62078547 }
  ?item wdt:P31 ?cls ;
        wdt:P17 wd:Q183 ;
        wdt:P856 ?site .
  FILTER NOT EXISTS { ?item wdt:P576 ?dissolved }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "de,en". }
}"""
# Fields we never want to crawl (art/music/theology/medicine-only schools, administrative bodies)
SKIP = re.compile(r"kunst|musik|music|theolog|kirchlich|verwaltung|polizei|bundeswehr|medizinische hochschule|"
                  r"film|schauspiel|design|tanz|katholische|evangelische|pflege|gesundheit|sport", re.I)


def main():
    r = http("GET", "https://query.wikidata.org/sparql", timeout=180,
             params={"query": QUERY, "format": "json"},
             headers={"Accept": "application/sparql-results+json"})
    if r is None or r.status_code != 200:
        print("Wikidata query failed", r.status_code if r is not None else "no response")
        return 1
    found = {}
    for b in r.json()["results"]["bindings"]:
        name = b["itemLabel"]["value"]
        if re.fullmatch(r"Q\d+", name) or SKIP.search(name):
            continue
        host = urlparse(b["site"]["value"]).netloc.lower().removeprefix("www.")
        if not host or host in found:
            continue
        found[host] = name
    path = os.path.join(ROOT, "companies.csv")
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    doms = {(x.get("domain") or "").lower().removeprefix("www.") for x in rows}
    names = {x["name"].lower() for x in rows}
    added = 0
    for host, name in sorted(found.items(), key=lambda kv: kv[1]):
        if host in doms or name.lower() in names:
            continue
        rows.append({"name": name, "domain": host, "type": "University / research", "ats": "", "ats_id": "",
                     "careers_url": "", "source": "Wikidata (universities + research institutes)",
                     "status": "active", "checked": ""})
        doms.add(host); names.add(name.lower()); added += 1
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for x in rows:
            w.writerow({k: x.get(k, "") for k in FIELDS})
    print(f"Wikidata: {len(found)} institutions with a website; {added} new rows; list now {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
