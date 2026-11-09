"""Build the company database (data/company_db.csv) from free public sources and feed the
crawl list (companies.csv).

Sources
  wikidata  German organisations with an official website (industry, HQ city)      ~15k
  osm       OpenStreetMap offices / works / industrial sites in Germany with a website
  gleif     every active German legal entity in the GLEIF LEI register (no websites) ~250k
  ba        employers seen in Arbeitsagentur results (data/ba_employers.csv)
  list      companies.csv itself (hand lists, seeds, research institutes)

Every company gets a relevance tier for the engineering profile (e-mobility, power electronics,
drives, controls, energy, research):
  A  relevant industry or name  -> career page checked every day
  B  other company with website -> career page checked once a week (rotation in crawl.py)
  C  no website known yet       -> kept in the database only

Usage: python build_db.py [--sources wikidata,osm,gleif,ba] [--no-merge]
"""
import argparse
import csv
import json
import os
import re
import time
from urllib.parse import urlparse

from crawler.common import http

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "data", "company_db.csv")
COMPANIES = os.path.join(ROOT, "companies.csv")
DB_FIELDS = ["name", "domain", "industry", "city", "legal_form", "lei", "sources", "tier", "kind"]
UA = {"User-Agent": "job-crawler/1.0 (open-source job crawler; github.com/Vijay-Shanker-Kurugod/New-job-Scraler)"}

RELEVANT = re.compile(
    r"automo|fahrzeug|vehicle|\bkfz|elektr|electr|elektron|semiconductor|halbleiter|energ|batter|akku|"
    r"antrieb|drive|motor|power|leistung|mobil|lade|charging|solar|wind|photovolt|wasserstoff|hydrogen|"
    r"maschinen|machine|anlagenbau|automation|automatisier|robot|mechatron|sensor|steuerung|regel|control|"
    r"engineering|ingenieur|technik|technolog|systems|systeme|aerospace|luftfahrt|raumfahrt|rail|bahn|"
    r"schienen|transport|nutzfahrzeug|truck|bus\b|traktor|agrar|medizintechnik|messtechnik|prüf|test|"
    r"simulation|software|embedded|forschung|research|institut|hochschule|universit|fraunhofer|helmholtz|"
    r"max-planck|leibniz|dlr|kit\b|tu\b|rwth|manufactur|fertigung|zulieferer|supplier|komponenten|"
    r"electrical equipment|electronic|industrial|metal|chemie|chemical|kunststoff|plastics", re.I)
RESEARCH = re.compile(r"universit|hochschule|fachhochschule|institut|forschung|research|fraunhofer|helmholtz|"
                      r"max-planck|leibniz|akademie|academy|college", re.I)
NOT_EMPLOYER = re.compile(r"\b(fonds|fund|spezial-?sondervermögen|sondervermögen|invest(ment)? ?(kg|gmbh)|"
                          r"verwaltungs|beteiligungs|holding|grundstück|immobilien|vermögensverwaltung|"
                          r"komplementär|e\.v\.|stiftung|wohnungseigentümer|gbr)\b", re.I)


def norm_domain(url):
    if not url:
        return ""
    url = url.strip().split()[0]
    if "://" not in url:
        url = "http://" + url
    host = urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")
    return host if "." in host else ""


def norm_name(n):
    n = re.sub(r"\b(gmbh|mbh|ag|se|kg|co|kgaa|ug|ohg|e\.k\.|& co\.?|haftungsbeschränkt|limited|ltd|inc)\b", "",
               (n or "").lower())
    return re.sub(r"[^a-z0-9äöüß]+", "", n)


def tier_for(name, industry, domain):
    if not domain:
        return "C"
    return "A" if RELEVANT.search(f"{name} {industry}") else "B"


# --- Wikidata ------------------------------------------------------------------------------------
WD_QUERIES = [
    # organisations with an industry
    """SELECT ?c ?name ?w ?ind ?city WHERE {{ ?c wdt:P17 wd:Q183; wdt:P856 ?w; wdt:P452 ?i.
       ?i rdfs:label ?ind FILTER(lang(?ind)="en")
       OPTIONAL {{ ?c rdfs:label ?name FILTER(lang(?name)="de") }}
       OPTIONAL {{ ?c wdt:P159 ?h. ?h rdfs:label ?city FILTER(lang(?city)="de") }} }} LIMIT {lim} OFFSET {off}""",
    # organisations with a legal form (GmbH, AG, SE, KG, ...)
    """SELECT ?c ?name ?w ?ind ?city WHERE {{ ?c wdt:P17 wd:Q183; wdt:P856 ?w; wdt:P1454 ?lf.
       ?lf rdfs:label ?ind FILTER(lang(?ind)="en")
       OPTIONAL {{ ?c rdfs:label ?name FILTER(lang(?name)="de") }}
       OPTIONAL {{ ?c wdt:P159 ?h. ?h rdfs:label ?city FILTER(lang(?city)="de") }} }} LIMIT {lim} OFFSET {off}""",
]


def wikidata(log=print):
    out = {}
    for qi, q in enumerate(WD_QUERIES):
        off, lim = 0, 5000
        while off < 200000:
            r = http("GET", "https://query.wikidata.org/sparql", params={"query": q.format(lim=lim, off=off)},
                     headers={**UA, "Accept": "application/sparql-results+json"}, timeout=180, retries=2)
            if r is None or r.status_code != 200:
                log(f"  wikidata q{qi} offset {off}: http {getattr(r, 'status_code', 'none')}")
                if lim > 1000:
                    lim //= 2; continue        # retry the same offset with a smaller page
                break
            rows = r.json()["results"]["bindings"]
            for b in rows:
                dom = norm_domain(b["w"]["value"])
                name = (b.get("name") or {}).get("value") or ""
                if not dom or not name:
                    continue
                rec = out.setdefault(dom, {"name": name, "domain": dom, "industry": set(), "city": "",
                                           "sources": {"wikidata"}})
                rec["industry"].add(b["ind"]["value"])
                rec["city"] = rec["city"] or (b.get("city") or {}).get("value", "")
            log(f"  wikidata q{qi} offset {off}: {len(rows)} rows, {len(out)} companies")
            if len(rows) < lim:
                break
            off += lim
            time.sleep(2)
    for rec in out.values():
        rec["industry"] = "; ".join(sorted(rec["industry"]))[:200]
    return list(out.values())


# --- OpenStreetMap -------------------------------------------------------------------------------
STATES = ["DE-BW", "DE-BY", "DE-BE", "DE-BB", "DE-HB", "DE-HH", "DE-HE", "DE-MV", "DE-NI", "DE-NW", "DE-RP",
          "DE-SL", "DE-SN", "DE-ST", "DE-SH", "DE-TH"]
OSM_Q = """[out:json][timeout:600];area["ISO3166-2"="{st}"]->.a;
(nwr["office"~"company|engineering|research|it|energy_supplier|telecommunication"]["website"](area.a);
 nwr["office"~"company|engineering|research|it|energy_supplier"]["contact:website"](area.a);
 nwr["man_made"="works"]["website"](area.a);
 nwr["industrial"]["website"](area.a);
 nwr["craft"~"electronics|electrician|metal_construction|tool_maker"]["website"](area.a););
out tags;"""


def osm(log=print):
    out = {}
    for st in STATES:
        for attempt in range(3):
            r = http("POST", "https://overpass-api.de/api/interpreter", data={"data": OSM_Q.format(st=st)},
                     headers={**UA, "Accept": "*/*"}, timeout=700, retries=0)
            if r is not None and r.status_code == 200:
                break
            log(f"  osm {st}: http {getattr(r, 'status_code', 'none')} (attempt {attempt + 1})")
            time.sleep(60)
        else:
            continue
        n = 0
        for e in r.json().get("elements", []):
            t = e.get("tags") or {}
            dom = norm_domain(t.get("website") or t.get("contact:website"))
            name = t.get("name") or t.get("operator") or ""
            if not dom or not name:
                continue
            kind = "; ".join(f"{k}={t[k]}" for k in ("office", "industrial", "man_made", "craft") if k in t)
            rec = out.setdefault(dom, {"name": name, "domain": dom, "industry": kind,
                                       "city": t.get("addr:city", ""), "sources": {"osm"}})
            n += 1
        log(f"  osm {st}: {n} tagged sites, {len(out)} companies so far")
        time.sleep(20)
    return list(out.values())


# --- GLEIF ---------------------------------------------------------------------------------------
REGIONS = STATES


def gleif(log=print):
    out = {}
    for reg in REGIONS:
        page = 1
        while True:
            r = http("GET", "https://api.gleif.org/api/v1/lei-records",
                     params={"filter[entity.legalAddress.country]": "DE", "filter[entity.legalAddress.region]": reg,
                             "filter[entity.status]": "ACTIVE", "page[size]": 200, "page[number]": page},
                     headers={**UA, "Accept": "application/vnd.api+json"}, timeout=60, retries=2)
            if r is None or r.status_code != 200:
                log(f"  gleif {reg} p{page}: http {getattr(r, 'status_code', 'none')}"); break
            d = r.json()
            for rec in d.get("data", []):
                e = (rec.get("attributes") or {}).get("entity") or {}
                if e.get("category") == "FUND":
                    continue
                name = ((e.get("legalName") or {}).get("name") or "").strip()
                if not name or NOT_EMPLOYER.search(name):
                    continue
                lei = rec.get("id")
                out[lei] = {"name": name, "domain": "", "industry": "", "lei": lei,
                            "city": (e.get("legalAddress") or {}).get("city", ""),
                            "legal_form": ((e.get("legalForm") or {}).get("id") or ""), "sources": {"gleif"}}
            last = ((d.get("meta") or {}).get("pagination") or {}).get("lastPage", page)
            if page >= last:
                break
            page += 1
            time.sleep(0.3)
        log(f"  gleif {reg}: {len(out)} entities so far")
    return list(out.values())


# --- Arbeitsagentur employers and the current crawl list ----------------------------------------
def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ba():
    return [{"name": r["name"], "domain": norm_domain(r.get("domain")), "industry": "", "city": "",
             "sources": {"arbeitsagentur"}} for r in load_csv(os.path.join(ROOT, "data", "ba_employers.csv"))]


def current_list():
    return [{"name": r["name"], "domain": norm_domain(r.get("domain")), "industry": r.get("type", ""),
             "city": "", "sources": {"crawl list"}, "kind": r.get("type", "")} for r in load_csv(COMPANIES)]


# --- merge ---------------------------------------------------------------------------------------
def merge(groups):
    by_dom, by_name = {}, {}
    for recs in groups:
        for r in recs:
            r = dict(r)
            r["sources"] = set(r["sources"]) if not isinstance(r["sources"], str) else set(r["sources"].split("|"))
            key = r["domain"] or ""
            nk = norm_name(r["name"])
            hit = by_dom.get(key) if key else None
            hit = hit or (by_name.get(nk) if nk and len(nk) > 3 else None)
            if hit:
                hit["sources"] |= r["sources"]
                for k in ("domain", "industry", "city", "legal_form", "lei", "kind"):
                    if not hit.get(k) and r.get(k):
                        hit[k] = r[k]
                if hit["domain"]:
                    by_dom[hit["domain"]] = hit
            else:
                if key:
                    by_dom[key] = r
                if nk:
                    by_name.setdefault(nk, r)
    seen, out = set(), []
    for r in list(by_dom.values()) + list(by_name.values()):
        if id(r) in seen:
            continue
        seen.add(id(r))
        r["tier"] = tier_for(r["name"], r.get("industry", ""), r.get("domain", ""))
        r["kind"] = r.get("kind") or ("University / research" if RESEARCH.search(r["name"] + " " + r.get("industry", ""))
                                      else "Industry")
        out.append(r)
    return out


def save_db(rows):
    with open(DB, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DB_FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["tier"], r["name"].lower())):
            w.writerow({**{k: r.get(k, "") for k in DB_FIELDS}, "sources": "|".join(sorted(r["sources"]))})


def feed_crawl_list(rows, log=print):
    """Append every database company with a website that is not in companies.csv yet."""
    from detect_ats import FIELDS
    have = load_csv(COMPANIES)
    doms = {norm_domain(r.get("domain")) for r in have}
    names = {norm_name(r["name"]) for r in have}
    added = 0
    for r in rows:
        if r["tier"] == "C" or r["domain"] in doms or norm_name(r["name"]) in names:
            continue
        have.append({"name": r["name"], "domain": r["domain"], "type": r["kind"], "ats": "", "ats_id": "",
                     "careers_url": "", "source": "DB: " + "+".join(sorted(r["sources"])), "status": "active",
                     "checked": "", "tier": r["tier"]})
        doms.add(r["domain"]); names.add(norm_name(r["name"])); added += 1
    for r in have:
        r.setdefault("tier", "")
        if not r["tier"]:
            r["tier"] = "A"       # hand lists, seeds and research institutes: daily
    with open(COMPANIES, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in have:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    log(f"crawl list: +{added} companies, {len(have)} total")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="wikidata,osm,gleif,ba")
    ap.add_argument("--no-merge", action="store_true", help="only rebuild data/company_db.csv")
    a = ap.parse_args()
    want = a.sources.split(",")
    groups = [current_list()]
    old = load_csv(DB)
    if old:
        groups.append([{**r, "sources": set(r["sources"].split("|"))} for r in old])
    for name, fn in (("wikidata", wikidata), ("osm", osm), ("gleif", gleif), ("ba", ba)):
        if name in want:
            t = time.time()
            recs = fn() if name == "ba" else fn(log=print)
            print(f"{name}: {len(recs)} records in {time.time() - t:.0f}s", flush=True)
            groups.append(recs)
    rows = merge(groups)
    save_db(rows)
    tiers = {t: sum(1 for r in rows if r["tier"] == t) for t in "ABC"}
    print(f"company_db: {len(rows)} companies  (A {tiers['A']}, B {tiers['B']}, C {tiers['C']} without website)")
    if not a.no_merge:
        feed_crawl_list(rows)


if __name__ == "__main__":
    main()
