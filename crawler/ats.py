"""Adapters for the public job feeds of common applicant-tracking systems (ATS).

Every function takes the company row (dict from companies.csv) and returns a list of
job records that are in Germany and pass the cheap relevance filter.
ats_id formats:
  greenhouse:      board token            e.g. isaraerospace
  lever:           company or eu:company  e.g. eu:helsing
  ashby:           job board name         e.g. proxima-fusion
  smartrecruiters: company identifier     e.g. BoschGroup
  personio:        subdomain              e.g. instagrid   (tries .de then .com)
  workday:         tenant|wdN|site        e.g. zf|wd3|careers
"""
import re
import time
import xml.etree.ElementTree as ET

MAX_AGE = 7  # days; sweep.py raises this to list every open job

from .common import (TODAY, get_json, http, is_germany, iso_date, job, relevant, strip_html, title_ok)

# Search terms used for big ATS that support server-side search (Workday, SmartRecruiters).
SEARCH_TERMS = ["Simulink", "MATLAB", "Leistungselektronik", "power electronics", "Hardware-in-the-Loop",
                "HiL", "Elektrotechnik", "electrical engineer", "Antrieb", "electric drive", "e-motor",
                "inverter", "Umrichter", "battery", "Batterie", "Hochvolt", "Regelungstechnik",
                "control engineer", "Validierung", "validation", "Simulation", "Fahrzeugtechnik",
                "Absolvent", "graduate", "Berufseinsteiger", "CANoe", "Elektromobilität", "charging",
                "Ladetechnik", "Funktionsentwicklung", "embedded"]


def _rec(row, **kw):
    return job(company=row["name"], ctype=row.get("type", ""), **kw)


# --- Greenhouse -----------------------------------------------------------------------
def greenhouse(row):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{row['ats_id']}/jobs", params={"content": "true"})
    out = []
    for j in (d or {}).get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if not is_germany(loc):
            continue
        desc = strip_html(j.get("content"))
        if relevant(j.get("title"), desc):
            out.append(_rec(row, jid=f"gh-{j['id']}", title=j["title"], location=loc, url=j.get("absolute_url"),
                            published=iso_date(j.get("first_published") or j.get("updated_at")),
                            desc=desc, source=f"Career site ({row['name']}, Greenhouse)"))
    return out


# --- Lever ------------------------------------------------------------------------------
def lever(row):
    ident = row["ats_id"]
    host = "api.lever.co"
    if ident.startswith("eu:"):
        host, ident = "api.eu.lever.co", ident[3:]
    d = get_json(f"https://{host}/v0/postings/{ident}", params={"mode": "json"}) or []
    out = []
    for j in d if isinstance(d, list) else []:
        cat = j.get("categories") or {}
        loc = ", ".join(filter(None, [cat.get("location")] + (cat.get("allLocations") or [])))
        if not is_germany(loc, j.get("country")):
            continue
        desc = (j.get("descriptionPlain") or "") + "\n" + "\n".join(
            f"{l.get('text')}: {strip_html(l.get('content'))}" for l in j.get("lists") or [])
        if relevant(j.get("text"), desc):
            out.append(_rec(row, jid=f"lv-{j['id']}", title=j["text"], location=loc, url=j.get("hostedUrl"),
                            published=iso_date(j.get("createdAt")), desc=desc,
                            source=f"Career site ({row['name']}, Lever)"))
    return out


# --- Ashby ------------------------------------------------------------------------------
def ashby(row):
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{row['ats_id']}",
                 params={"includeCompensation": "false"})
    out = []
    for j in (d or {}).get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        loc = ", ".join(filter(None, locs + [addr.get("addressCountry")]))
        if not is_germany(loc):
            continue
        desc = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml"))
        if relevant(j.get("title"), desc):
            out.append(_rec(row, jid=f"ab-{j['id']}", title=j["title"], location=loc, url=j.get("jobUrl"),
                            published=iso_date(j.get("publishedAt")), desc=desc,
                            source=f"Career site ({row['name']}, Ashby)"))
    return out


# --- SmartRecruiters --------------------------------------------------------------------
def smartrecruiters(row):
    base = f"https://api.smartrecruiters.com/v1/companies/{row['ats_id']}/postings"
    out, seen, offset = [], set(), 0
    while offset < 2000:
        d = get_json(base, params={"country": "de", "limit": 100, "offset": offset})
        items = (d or {}).get("content") or []
        for j in items:
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            loc_o = j.get("location") or {}
            loc = ", ".join(filter(None, [loc_o.get("city"), loc_o.get("region"), loc_o.get("country")]))
            pub = iso_date(j.get("releasedDate"))
            if not title_ok(j.get("name")):
                continue
            age = (TODAY.toordinal() - _ord(pub)) if pub else 0
            if age > MAX_AGE + 3:  # skip detail call for old postings
                continue
            det = get_json(j.get("ref") or f"{base}/{j['id']}") or {}
            secs = ((det.get("jobAd") or {}).get("sections") or {})
            desc = "\n".join(strip_html((secs.get(k) or {}).get("text")) for k in
                             ("jobDescription", "qualifications", "additionalInformation"))
            if relevant(j.get("name"), desc):
                out.append(_rec(row, jid=f"sr-{j['id']}", title=j["name"], location=loc,
                                url=det.get("postingUrl") or f"https://jobs.smartrecruiters.com/{row['ats_id']}/{j['id']}",
                                published=pub, desc=desc, source=f"Career site ({row['name']}, SmartRecruiters)"))
            time.sleep(0.15)
        if len(items) < 100:
            break
        offset += 100
    return out


def _ord(date_str):
    import datetime as dt
    try:
        return dt.date.fromisoformat(date_str).toordinal()
    except ValueError:
        return TODAY.toordinal()


# --- Personio ---------------------------------------------------------------------------
def personio(row):
    for tld in ("de", "com"):
        r = http("GET", f"https://{row['ats_id']}.jobs.personio.{tld}/xml", params={"language": "de"})
        if r is not None and r.status_code == 200 and b"<workzag-jobs" in r.content[:500]:
            break
    else:
        return []
    out = []
    root = ET.fromstring(r.content)
    for p in root.findall("position"):
        g = lambda t: (p.findtext(t) or "").strip()
        offices = [g("office")] + [o.text or "" for o in p.findall("additionalOffices/office")]
        loc = ", ".join(filter(None, offices))
        # Personio is used almost only by DACH firms; accept unknown offices, reject clear non-DE.
        if loc and not is_germany(loc) and re.search(r"austria|österreich|wien|vienna|schweiz|switzerland|zürich|zurich|"
                                                    r"london|paris|madrid|warsaw|prague|amsterdam|remote \(?(uk|us)",
                                                    loc, re.I):
            continue
        desc = "\n".join(f"{(d.findtext('name') or '').strip()}: {strip_html(d.findtext('value'))}"
                         for d in p.findall("jobDescriptions/jobDescription"))
        if relevant(g("name"), desc):
            out.append(_rec(row, jid=f"pe-{row['ats_id']}-{g('id')}", title=g("name"), location=loc or "Deutschland",
                            url=f"https://{row['ats_id']}.jobs.personio.{tld}/job/{g('id')}",
                            published=iso_date(g("createdAt")), desc=desc,
                            source=f"Career site ({row['name']}, Personio)"))
    return out


# --- Workday ----------------------------------------------------------------------------
WD_AGE = re.compile(r"(today|heute)|(yesterday|gestern)|(\d+)\+?\s*(days?|tag)", re.I)


def _wd_age(posted_on):
    m = WD_AGE.search(posted_on or "")
    if not m:
        return None
    if m.group(1):
        return 0
    if m.group(2):
        return 1
    n = int(m.group(3))
    return 999 if "+" in posted_on else n


def workday(row, terms=SEARCH_TERMS):
    import datetime as dt
    tenant, wd, site = row["ats_id"].split("|")
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    found = {}
    for term in terms:
        for offset in range(0, 200, 20):
            r = http("POST", f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                                  "searchText": term})
            if r is None or r.status_code != 200:
                break
            posts = (r.json() or {}).get("jobPostings") or []
            for p in posts:
                age = _wd_age(p.get("postedOn"))
                if age is not None and age > MAX_AGE:
                    continue
                if not title_ok(p.get("title")):
                    continue
                found.setdefault(p.get("externalPath"), (p, age))
            if len(posts) < 20:
                break
            time.sleep(0.2)
    out = []
    for path, (p, age) in found.items():
        det = get_json(f"{api}{path}") or {}
        info = det.get("jobPostingInfo") or {}
        loc = ", ".join(filter(None, [info.get("location") or p.get("locationsText")] +
                               (info.get("additionalLocations") or []) +
                               [((info.get("country") or {}).get("descriptor"))]))
        if not is_germany(loc):
            continue
        desc = strip_html(info.get("jobDescription"))
        if relevant(p.get("title"), desc):
            pub = iso_date(info.get("startDate")) or (
                (TODAY - dt.timedelta(days=age)).isoformat() if age is not None else "")
            out.append(_rec(row, jid=f"wd-{tenant}-{info.get('jobReqId') or path.rsplit('_', 1)[-1]}",
                            title=p["title"], location=loc,
                            url=info.get("externalUrl") or f"{host}/{site}{path}",
                            published=pub, desc=desc, source=f"Career site ({row['name']}, Workday)"))
        time.sleep(0.15)
    return out


# --- Shared helpers for sitemap-based career sites ----------------------------------------
import datetime as _dt
from urllib.parse import unquote

LOC_RX = re.compile(r"<url>\s*<loc>([^<]+)</loc>(?:\s*<lastmod>([^<]+)</lastmod>)?", re.S)
SITEMAP_IDX = re.compile(r"<sitemap>\s*<loc>([^<]+)</loc>", re.S)
DETAIL_CAP = 60           # max job pages fetched per site and run (newest ids first)


def _sitemap_urls(url, depth=0):
    r = http("GET", url, timeout=40)
    if r is None or r.status_code != 200:
        return []
    t = r.text
    out = [(H_un(u), lm or "") for u, lm in LOC_RX.findall(t)]
    if depth < 1:
        for sub in SITEMAP_IDX.findall(t)[:12]:
            sub = H_un(sub)
            if re.search(r"(^|[/-])(de|en)[-_]?sitemap|sitemap\d*\.xml|job", sub, re.I) or len(out) == 0:
                out += _sitemap_urls(sub, depth + 1)
    return out


def H_un(s):
    return s.replace("&amp;", "&").replace("&apos;", "'").strip()


def _num_id(url):
    m = re.findall(r"(\d{3,})", url)
    return int(m[-1]) if m else 0


def _sf_date(s):
    """SuccessFactors datePosted: 'Mon Nov 02 02:00:00 UTC 2026' or ISO."""
    if not s:
        return ""
    try:
        return _dt.datetime.strptime(s.strip(), "%a %b %d %H:%M:%S UTC %Y").date().isoformat()
    except ValueError:
        return iso_date(s)


# --- SAP SuccessFactors Career Site Builder (VW, ZF, Schaeffler, MAN, MAHLE, AVL, ...) ---
# ats_id = career-site host, e.g. jobs.zf.com. Every CSB site publishes /sitemap.xml with all jobs
# (/job/<City>-<Title>-<PLZ>/<id>/); the job page carries datePosted, addressCountry and the text.
SF_DE = re.compile(r"\b(BW|BY|BE|BB|HB|HH|HE|MV|NI|NW|NRW|RP|SL|SN|ST|SH|TH)\s+\d{5}\s*$|\bD[- ]?\d{5}\s*$|\s\d{5}$")
SF_PROP = lambda name: re.compile(r'itemprop="%s"[^>]*content="([^"]*)"' % name)


def successfactors(row):
    host = row["ats_id"].strip("/")
    urls = [u for u, _ in _sitemap_urls(f"https://{host}/sitemap.xml") if "/job/" in u]
    cands = []
    for u in urls:
        slug = unquote(u.rstrip("/").rsplit("/job/", 1)[-1].rsplit("/", 1)[0]).replace("-", " ")
        # CSB slugs end in "<state code> <PLZ>" (e.g. "BY 97424"); keep German ones before the detail cap
        if title_ok(slug) and (SF_DE.search(slug) or is_germany(slug)):
            cands.append((u, slug))
    cands.sort(key=lambda c: _num_id(c[0]), reverse=True)
    out = []
    for u, slug in cands[:DETAIL_CAP]:
        r = http("GET", u, timeout=30)
        if r is None or r.status_code != 200:
            continue
        h = r.text
        country = (SF_PROP("addressCountry").findall(h) or [""])[0]
        city = (SF_PROP("addressLocality").findall(h) or [""])[0]
        if country and country.upper() not in ("DE", "DEU", "GERMANY", "DEUTSCHLAND"):
            continue
        if not country and not is_germany(slug):
            continue
        title = (re.findall(r'itemprop="title"[^>]*>([^<]+)<', h) or
                 re.findall(r"<title>([^<|]+)", h) or [slug])[0].strip()
        title = re.sub(r"\s*(Stellendetails|Job Details|Jobdetails)\s*$", "", title).strip()
        pub = _sf_date((SF_PROP("datePosted").findall(h) or [""])[0])
        i = h.find('class="jobdescription"')
        i = i if i >= 0 else h.find('itemprop="description"')
        desc = strip_html(h[i:i + 12000].split('class="applylink', 1)[0]) if i >= 0 else ""
        if relevant(title, desc):
            out.append(_rec(row, jid=f"sf-{host}-{_num_id(u)}", title=title,
                            location=", ".join(filter(None, [city, "Deutschland"])), url=u,
                            published=pub, desc=desc, source=f"Career site ({row['name']}, SuccessFactors)"))
        time.sleep(0.2)
    return out


# --- Generic job sitemap (Porsche etc.): sitemap url in ats_id, details via JSON-LD --------
def sitemap(row):
    from .generic import _ld_postings, _ld_location
    sm = row["ats_id"]
    urls = [u for u, _ in _sitemap_urls(sm) if re.search(r"job|stelle|position|vacanc|ac=jobad", u, re.I)]
    urls.sort(key=_num_id, reverse=True)
    out = []
    for u in urls[:DETAIL_CAP]:
        r = http("GET", u, timeout=30)
        if r is None or r.status_code != 200:
            continue
        for p in _ld_postings(r.text)[:1]:
            title = strip_html(p.get("title") or "")
            loc = _ld_location(p)
            if loc and not is_germany(loc):
                continue
            desc = strip_html(H_un(p.get("description") or ""))
            if relevant(title, desc):
                out.append(_rec(row, jid=f"sm-{row['name'][:20]}-{_num_id(u)}", title=title,
                                location=loc or "Deutschland", url=u, published=iso_date(p.get("datePosted")),
                                desc=desc, source=f"Career site ({row['name']})"))
        time.sleep(0.2)
    return out


# --- Eightfold (Infineon, ...): ats_id = host|domain, e.g. jobs.infineon.com|infineon.com --------
EF_TERMS = ["power electronics", "Leistungselektronik", "Simulink", "inverter", "motor control", "HiL",
            "validation", "system engineer", "application engineer", "graduate", "Absolvent", "embedded",
            "automotive", "Elektrotechnik", "control"]


def eightfold(row, terms=EF_TERMS):
    host, domain = row["ats_id"].split("|")
    found = {}
    for term in terms:
        for start in (0, 10, 20):
            d = get_json(f"https://{host}/api/pcsx/search",
                         params={"domain": domain, "query": term, "location": "Germany", "start": start})
            pos = ((d or {}).get("data") or {}).get("positions") or []
            for p in pos:
                locs = ", ".join(p.get("standardizedLocations") or p.get("locations") or [])
                if not re.search(r",\s*DE\b", locs) and not is_germany(locs):
                    continue
                if title_ok(p.get("name")):
                    found.setdefault(p["id"], (p, locs))
            if len(pos) < 10:
                break
            time.sleep(0.2)
    out = []
    for pid, (p, locs) in found.items():
        pub = iso_date(p.get("postedTs") or p.get("creationTs"))
        if pub and (TODAY - _dt.date.fromisoformat(pub)).days > MAX_AGE:
            continue
        out.append(_rec(row, jid=f"ef-{domain}-{pid}", title=p["name"], location=locs,
                        url=f"https://{host}{p.get('positionUrl') or '/careers/job/' + str(pid)}",
                        published=pub, desc="", source=f"Career site ({row['name']}, Eightfold)"))
    return out


# --- d.vinci (FEV, ...): ats_id = career host, list at /jobPublication/list.json -------------
def dvinci(row):
    d = get_json(f"https://{row['ats_id']}/jobPublication/list.json") or []
    out = []
    for j in d if isinstance(d, list) else []:
        title = j.get("position") or ""
        locs = [j.get("subtitle") or ""]
        for k in ("locations", "workplaces", "jobOpening"):
            v = j.get(k)
            locs.append(str(v)[:400] if v else "")
        loc = " ".join(locs)
        if not is_germany(loc):
            continue
        desc = strip_html(" ".join(str(j.get(k) or "") for k in ("introduction", "tasks", "profile", "weOffer")))
        if relevant(title, desc):
            pub = iso_date(j.get("startDate") or j.get("publishedDate") or j.get("createdDate"))
            out.append(_rec(row, jid=f"dv-{row['ats_id']}-{j.get('id')}", title=title,
                            location=(j.get("subtitle") or "Deutschland").replace("Location:", "").strip(),
                            url=j.get("jobPublicationURL"), published=pub, desc=desc,
                            source=f"Career site ({row['name']}, d.vinci)"))
    return out


# --- beesite job API (Mercedes-Benz): ats_id = API search URL ---------------------------------
# The API ignores plain query params; it takes one JSON "data" param. We page newest-first and stop
# at postings older than MAX_AGE, so every new Mercedes job in Germany is seen, not just search hits.
import json as _json
BS_FIELDS = ["ID", "PositionTitle", "PositionURI", "PositionLocation.CountryName", "PositionLocation.CityName",
             "PositionLocation.CountryCode", "PublicationStartDate", "PositionFormattedDescription.Content",
             "CareerLevel.Name", "OrganizationName"]


def beesite(row, max_pages=15):
    out = []
    for page in range(max_pages):
        q = {"LanguageCode": "DE", "SearchParameters": {
                "FirstItem": 1 + 100 * page, "CountItem": 100, "MatchedObjectDescriptor": BS_FIELDS,
                "Sort": [{"Criterion": "PublicationStartDate", "Direction": "DESC"}]}, "SearchCriteria": []}
        d = get_json(row["ats_id"], params={"data": _json.dumps(q)})
        items = (((d or {}).get("SearchResult") or {}).get("SearchResultItems")) or []
        stop = False
        for it in items:
            m = it.get("MatchedObjectDescriptor") or {}
            pub = iso_date(m.get("PublicationStartDate"))
            if pub and (TODAY - _dt.date.fromisoformat(pub)).days > MAX_AGE:
                stop = True
                continue
            locs = m.get("PositionLocation") or []
            loc = ", ".join(filter(None, [" ".join(filter(None, [l.get("CityName"), l.get("CountryName")]))
                                          for l in locs]))
            if not any((l.get("CountryCode") or "").upper() == "DE" or is_germany(l.get("CountryName") or "")
                       for l in locs):
                continue
            title = m.get("PositionTitle") or ""
            desc = strip_html(" ".join(str((x or {}).get("Content") or "") for x in
                                       (m.get("PositionFormattedDescription") or [])))
            if relevant(title, desc) or (not desc and title_ok(title)):
                out.append(_rec(row, jid=f"bs-{m.get('ID') or it.get('MatchedObjectId')}", title=title,
                                location=loc, url=m.get("PositionURI") or "https://jobs.mercedes-benz.com",
                                published=pub, desc=desc, source=f"Career site ({row['name']})"))
        if stop or len(items) < 100:
            break
        time.sleep(0.3)
    return out


ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
            "personio": personio, "workday": workday, "successfactors": successfactors, "sitemap": sitemap,
            "eightfold": eightfold, "dvinci": dvinci, "beesite": beesite}
