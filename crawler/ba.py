"""Bundesagentur für Arbeit Jobsuche API (official, free, public API key).

Covers every employer that posts on the Jobbörse, which is free for employers, so most
SMEs and many universities are here.
Verified 30 Oct 2026: search = /pc/v6/jobs (field names below); description =
/pc/v4/jobdetails/<base64(referenznummer)>; the older /pc/v4/jobs search returns 403.
"""
import base64
import time
from urllib.parse import urlparse

from .common import contacts, get_json, iso_date, job, relevant, strip_html, title_ok

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
HDR = {"X-API-Key": "jobboerse-jobsuche"}
UNI = ("universität", "hochschule", "university", "fraunhofer", "dlr", "helmholtz", "max-planck", "leibniz",
       "institut", "forschungszentrum", "karlsruher institut", "rwth", "technische universität")
BOARDS = ("arbeitsagentur", "stepstone", "indeed", "linkedin", "xing", "monster", "jobware", "stellenanzeigen",
          "kimeta", "jobvector", "softgarden", "personio", "smartrecruiters", "workday", "greenhouse", "lever.co",
          "join.com", "onapply", "dvinci", "rexx", "jobs.b-ite", "connectoor", "heyjobs", "meinestadt")


def _search(keyword, days, max_pages, log):
    out = []
    for page in range(1, max_pages + 1):
        data = get_json(f"{BASE}/pc/v6/jobs", headers=HDR, params={
            "was": keyword, "veroeffentlichtseit": days, "angebotsart": 1,  # 1 = Arbeit
            "page": page, "size": 100})
        if data is None:
            log(f"  BA '{keyword}' page {page}: request failed")
            break
        items = data.get("ergebnisliste") or []
        out += items
        if len(items) < 100 or page * 100 >= int(data.get("maxErgebnisse") or 0):
            break
        time.sleep(0.3)
    return out


def _details(refnr):
    key = base64.b64encode(refnr.encode()).decode()
    d = get_json(f"{BASE}/pc/v4/jobdetails/{key}", headers=HDR) or {}
    return strip_html(d.get("stellenangebotsBeschreibung") or ""), d


def _domain(url):
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = host[4:] if host.startswith("www.") else host
    return "" if any(b in host for b in BOARDS) else host


def _urls(d):
    for k, v in d.items():
        if isinstance(v, str) and v.startswith("http") and "url" in k.lower():
            yield v


def crawl(keywords, days=7, max_pages=5, log=print):
    """Returns (jobs, employers). employers = {name: domain} for the employer list."""
    raw = {}
    for kw in keywords:
        items = _search(kw, days, max_pages, log)
        log(f"  BA '{kw}': {len(items)}")
        for it in items:
            raw.setdefault(it.get("referenznummer"), it)
    jobs, employers = [], {}
    for refnr, it in raw.items():
        if not refnr:
            continue
        title = it.get("stellenangebotsTitel") or it.get("hauptberuf") or ""
        emp = it.get("firma") or ""
        employers.setdefault(emp, "")
        if not title_ok(title):
            continue
        locs = []
        for l in it.get("stellenlokationen") or []:
            a = l.get("adresse") or {}
            locs.append(" ".join(x for x in (a.get("plz"), a.get("ort")) if x))
        loc = "; ".join(filter(None, locs)) or "Deutschland"
        desc, det = _details(refnr)
        time.sleep(0.2)
        for u in _urls(det):
            dom = _domain(u)
            if dom:
                employers[emp] = dom
                break
        if desc and not relevant(title, desc):
            continue
        ctype = "University / research" if any(u in emp.lower() for u in UNI) else ""
        published = iso_date(it.get("datumErsteVeroeffentlichung")
                             or (it.get("veroeffentlichungszeitraum") or {}).get("von") or it.get("aenderungsdatum"))
        sal = ""
        if it.get("gehaltsspanneVon"):
            sal = f"{int(it['gehaltsspanneVon'])}-{int(it.get('gehaltsspanneBis') or 0)} EUR/{it.get('verguetungsangabe', '')}"
        jobs.append(job(
            jid=f"ba-{refnr}", title=title, company=emp, location=loc,
            url=f"https://www.arbeitsagentur.de/jobsuche/suche?id={refnr}",
            published=published, desc=desc, source="Arbeitsagentur", ctype=ctype,
            extra={**({"salaryInfo": sal} if sal else {}), **contacts(desc, _urls(det))}))
    return jobs, employers
