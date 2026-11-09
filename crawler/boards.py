"""Public job boards and research-institute feeds (all free, no login).

Each reader returns a list of job records (crawler.common.job). Jobs carry publishedAt when the
board shows a date; crawl.py drops anything older than MAX_AGE days and remembers every id.

Sources: LinkedIn (guest search, DE + Bengaluru + France), StepStone, XING, Jobware
(also feeds jobs.ingenieur.de), academics.de (also feeds jobs.zeit.de), service.bund.de
(federal + state job markets incl. universities), Fraunhofer + DLR (SuccessFactors RSS),
EURAXESS (research jobs, Germany only), Arbeitnow (startup / English-language jobs).
Blocked for robots (checked 4 Nov 2026): Indeed, startup.jobs, jobvector, Monster, Jooble,
Glassdoor, Hochschulkompass.
"""
import html as H
import json
import re
import time
from urllib.parse import quote, quote_plus

from .common import age_days, http, iso_date, job, strip_html, title_ok

MAX_AGE = 7
DEADLINE = [0.0]          # epoch seconds; readers stop fetching once passed (set by crawl_all)
UA_HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def _get(url, timeout=40, **kw):
    if DEADLINE[0] and time.time() > DEADLINE[0]:
        return None
    r = http("GET", url, timeout=timeout, retries=1, headers=UA_HTML, **kw)
    if r is None or r.status_code != 200:
        return None
    if "charset" not in r.headers.get("Content-Type", "").lower():
        r.encoding = "utf-8"
    return r.text


def _fresh(date):
    a = age_days(date)
    return a is None or a <= MAX_AGE


def _text(s):
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ---------- LinkedIn (guest API) -------------------------------------------------------------
LI_SEARCH = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
             "keywords={kw}&location={loc}&f_TPR=r604800&start={start}")
LI_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
CARD = re.compile(r"<li>(.*?)</li>", re.S)


def linkedin(keywords, location="Germany", pages=4, seen=None, log=print, source="LinkedIn"):
    out, ids = [], set()
    seen = seen or {}
    for kw in keywords:
        for p in range(pages):
            txt = _get(LI_SEARCH.format(kw=quote(kw), loc=quote(location), start=p * 10), timeout=30)
            time.sleep(1.2)
            if not txt:
                break
            cards = CARD.findall(txt)
            if not cards:
                break
            for c in cards:
                m = re.search(r"jobPosting:(\d+)|/jobs/view/[^\"?]*?-(\d+)[?\"]", c)
                if not m:
                    continue
                jid = m.group(1) or m.group(2)
                if jid in ids:
                    continue
                ids.add(jid)
                title = _text((re.search(r'base-search-card__title[^>]*>(.*?)</h3>', c, re.S) or [None, ""])[1])
                comp = _text((re.search(r'base-search-card__subtitle[^>]*>(.*?)</h4>', c, re.S) or [None, ""])[1])
                loc = _text((re.search(r'job-search-card__location[^>]*>(.*?)</span>', c, re.S) or [None, ""])[1])
                date = (re.search(r'datetime="([\d-]+)"', c) or [None, ""])[1]
                if not title_ok(title) or not _fresh(date):
                    continue
                out.append(job(jid=jid, title=title, company=comp, location=loc, published=date,
                               url=f"https://www.linkedin.com/jobs/view/{jid}", source=source))
            if len(cards) < 10:
                break
    # descriptions only for jobs the crawler has not emitted before
    todo = [j for j in out if j["jobId"] not in seen]
    fails = 0
    for j in todo[:300]:
        if fails >= 5:
            break
        txt = _get(LI_DETAIL.format(id=j["jobId"]), timeout=20)
        time.sleep(1.0)
        if not txt:
            fails += 1; continue
        fails = 0
        m = re.search(r'show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', txt, re.S)
        j["jobDescription"] = strip_html(m.group(1)) if m else ""
    log(f"  LinkedIn {location}: {len(out)} listed, {len(todo)} new described")
    return out


# ---------- StepStone ------------------------------------------------------------------------
def stepstone(keywords, pages=3, log=print):
    out, ids = [], set()
    for kw in keywords:
        slug = re.sub(r"[^a-z0-9äöüß]+", "-", kw.lower()).strip("-")
        for p in range(1, pages + 1):
            url = f"https://www.stepstone.de/jobs/{quote(slug)}?ag=age_7" + (f"&page={p}" if p > 1 else "")
            txt = _get(url, timeout=60)
            time.sleep(1.5)
            if not txt:
                break
            m = re.search(r'window\.__PRELOADED_STATE__\["app-unifiedResultlist"\]\s*=\s*', txt)
            if not m:
                break
            try:
                obj, _ = json.JSONDecoder().raw_decode(txt[m.end():])
                items = obj["searchResults"]["items"]
            except (ValueError, KeyError, TypeError):
                break
            for it in items:
                jid = f"ss-{it.get('id')}"
                if jid in ids:
                    continue
                ids.add(jid)
                date = iso_date(it.get("datePosted"))
                if not title_ok(it.get("title")) or not _fresh(date):
                    continue
                link = (it.get("url") or "").split("?")[0]
                out.append(job(jid=jid, title=it.get("title"), company=it.get("companyName", ""),
                               location=it.get("location", ""), published=date, url=link,
                               desc=strip_html(it.get("textSnippet", "")), source="StepStone"))
            if len(items) < 25:
                break
    log(f"  StepStone: {len(out)}")
    return out


# ---------- XING -----------------------------------------------------------------------------
def xing(keywords, seen=None, log=print):
    out, ids = [], set()
    seen = seen or {}
    for kw in keywords:
        txt = _get(f"https://www.xing.com/jobs/search?keywords={quote_plus(kw)}", timeout=40)
        time.sleep(1.2)
        if not txt:
            continue
        for m in re.finditer(r'href="(/jobs/[a-z0-9-]+-(\d{6,}))"[^>]*>(.*?)</a>', txt, re.S):
            path, num, inner = m.groups()
            jid = f"xg-{num}"
            if jid in ids or jid in seen:
                continue
            ids.add(jid)
            title = _text(inner)
            if title and not title_ok(title):
                continue
            out.append({"path": path, "jid": jid})
    jobs, fails = [], 0
    for x in out[:250]:
        if fails >= 5:
            break
        txt = _get("https://www.xing.com" + x["path"], timeout=20)
        time.sleep(1.0)
        if not txt:
            fails += 1; continue
        fails = 0
        d = _ld_jobposting(txt)
        if not d:
            continue
        date = iso_date(d.get("datePosted"))
        if not title_ok(d.get("title")) or not _fresh(date):
            continue
        jobs.append(job(jid=x["jid"], title=d.get("title"), company=_org(d), location=_place(d),
                        published=date, url="https://www.xing.com" + x["path"],
                        desc=strip_html(d.get("description", "")), source="XING"))
    log(f"  XING: {len(out)} candidates, {len(jobs)} kept")
    return jobs


# ---------- Jobware (+ jobs.ingenieur.de) -----------------------------------------------------
def jobware(keywords, log=print):
    out, ids = [], set()
    for kw in keywords:
        slug = re.sub(r"[^a-z0-9]+", "-", kw.lower().replace("ä", "ae").replace("ö", "oe")
                      .replace("ü", "ue").replace("ß", "ss")).strip("-")
        txt = _get(f"https://www.jobware.de/jobs--fuer-{slug}", timeout=40)
        time.sleep(1.2)
        if not txt:
            continue
        for art in re.findall(r"<article\b.*?</article>", txt, re.S):
            m = re.search(r'href="(/job/[a-z0-9-]+-(\d{6,}))"', art)
            if not m:
                continue
            jid = f"jw-{m.group(2)}"
            if jid in ids:
                continue
            ids.add(jid)
            title = _text((re.search(r"<h[23][^>]*>(.*?)</h[23]>", art, re.S) or [None, ""])[1])
            date = iso_date((re.search(r'dateTime="([^"]+)"', art, re.I) or [None, ""])[1])
            spans = [_text(s) for s in re.findall(r'tag__text-content"[^>]*>(.*?)</span>\s*<span[^>]*tag__affix',
                                                  art, re.S)]
            loc = next((s[3:] for s in spans if s.startswith("in ")), "")
            comp = _text((re.search(r'job-card__advertiser-name"[^>]*>(.*?)</span>', art, re.S) or [None, ""])[1])
            if not title_ok(title) or not _fresh(date):
                continue
            out.append(job(jid=jid, title=title, company=comp, location=loc, published=date,
                           url="https://www.jobware.de" + m.group(1), source="Jobware"))
    log(f"  Jobware: {len(out)}")
    return out


# ---------- academics.de (+ jobs.zeit.de) ---------------------------------------------------
def academics(keywords, seen=None, log=print):
    cand, ids = [], set()
    seen = seen or {}
    for kw in keywords:
        txt = _get(f"https://www.academics.de/stellenanzeigen?q={quote_plus(kw)}&sort=published_at_desc",
                   timeout=40)
        time.sleep(1.2)
        if not txt:
            continue
        for m in re.finditer(r'href="(/jobs/[a-z0-9-]+-(\d{6,}))"[^>]*title="([^"]*)"', txt):
            path, num, title = m.groups()
            jid = f"ac-{num}"
            if jid in ids or jid in seen:
                continue
            ids.add(jid)
            if title_ok(H.unescape(title)):
                cand.append((jid, path))
    jobs = []
    for jid, path in cand[:200]:
        txt = _get("https://www.academics.de" + path, timeout=40)
        time.sleep(1.0)
        d = _ld_jobposting(txt or "")
        if not d:
            continue
        date = iso_date(d.get("datePosted"))
        if not _fresh(date):
            continue
        jobs.append(job(jid=jid, title=d.get("title"), company=_org(d), location=_place(d), published=date,
                        url="https://www.academics.de" + path, desc=strip_html(d.get("description", "")),
                        source="academics.de", ctype="University / research"))
    log(f"  academics.de: {len(cand)} candidates, {len(jobs)} kept")
    return jobs


# ---------- service.bund.de (public sector, universities, Länder job markets) -----------------
def bund(keywords, log=print):
    out, ids = [], set()
    for kw in keywords:
        url = ("https://www.service.bund.de/Content/Globals/Functions/RSSFeed/RSSGenerator_Stellen.xml?"
               f"nn=4642046&templateQueryString={quote_plus(kw)}")
        txt = _get(url, timeout=60)
        time.sleep(1.0)
        if not txt:
            continue
        for it in re.findall(r"<item>(.*?)</item>", txt, re.S):
            title = _text((re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1])
            link = _text((re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1]).split("#")[0]
            pub = (re.search(r"<pubDate>(.*?)</pubDate>", it, re.S) or [None, ""])[1]
            desc = H.unescape((re.search(r"<description>(.*?)</description>", it, re.S) or [None, ""])[1])
            comp = _text((re.search(r"Arbeitgeber:\s*<strong>(.*?)</strong>", desc, re.S) or [None, ""])[1])
            loc = _text((re.search(r"Ort:\s*<strong>(.*?)</strong>", desc, re.S) or [None, ""])[1])
            date = _rfc822(pub)
            jid = "bd-" + re.sub(r"\W+", "", link.split("/")[-1].replace(".html", ""))[-40:]
            if jid in ids or not title_ok(title) or not _fresh(date):
                continue
            ids.add(jid)
            out.append(job(jid=jid, title=title, company=comp, location=loc, published=date, url=link,
                           source="service.bund.de", ctype="Public / university"))
    log(f"  service.bund.de: {len(out)}")
    return out


# ---------- SuccessFactors RSS (Fraunhofer, DLR) --------------------------------------------
SF_FEEDS = {"Fraunhofer": "https://jobs.fraunhofer.de/services/rss/job/?locale=de_DE&keywords={kw}",
            "DLR": "https://jobs.dlr.de/services/rss/job/?locale=de_DE&keywords={kw}"}


def sf_rss(keywords, log=print):
    out, ids = [], set()
    for org, tpl in SF_FEEDS.items():
        n = 0
        for kw in keywords:
            txt = _get(tpl.format(kw=quote(kw)), timeout=40)
            time.sleep(1.0)
            if not txt:
                continue
            for it in re.findall(r"<item>(.*?)</item>", txt, re.S):
                title = _text(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(r"<title>(.*?)</title>", it, re.S)
                                                                 or [None, ""])[1]))
                link = _text((re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1]).split("?")[0]
                desc = re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(r"<description>(.*?)</description>", it, re.S)
                                                         or [None, ""])[1])
                date = _rfc822((re.search(r"<pubDate>(.*?)</pubDate>", it, re.S) or [None, ""])[1])
                m = re.search(r"/(\d{6,})/?$", link)
                jid = f"sf-{org[:2].lower()}-{m.group(1) if m else link[-30:]}"
                loc = (re.search(r"\(([^()]*,\s*DE[^()]*)\)\s*$", title) or [None, ""])[1]
                clean = re.sub(r"\s*\([^()]*,\s*DE[^()]*\)\s*$", "", title)
                if jid in ids or not title_ok(clean) or not _fresh(date):
                    continue
                ids.add(jid); n += 1
                out.append(job(jid=jid, title=clean, company=org, location=loc or "Germany", published=date,
                               url=link, desc=strip_html(desc), source=org, ctype="University / research"))
        log(f"  {org}: {n}")
    return out


# ---------- EURAXESS (research jobs; keep Germany only) ---------------------------------------
def euraxess(keywords, log=print):
    out, ids = [], set()
    for kw in keywords:
        txt = _get(f"https://euraxess.ec.europa.eu/jobs/search?keywords={quote_plus(kw)}", timeout=40)
        time.sleep(1.0)
        if not txt:
            continue
        blocks = re.split(r'(?=<div id="job-teaser-content")', txt)
        for b in blocks[1:]:
            m = re.search(r'href="(/jobs/(\d+))"[^>]*>(.*?)</a>', b, re.S)
            labels = re.findall(r'ecl-label--highlight"\s*>([^<]+)<', b)
            if not m or not any("Germany" in x for x in labels):
                continue
            jid = f"ex-{m.group(2)}"
            title = _text(m.group(3))
            date = _dmy((re.search(r"Posted on:\s*(\d{1,2} \w+ \d{4})", _text(b)) or [None, ""])[1])
            if jid in ids or not title_ok(title) or not _fresh(date):
                continue
            ids.add(jid)
            org = _text((re.search(r'<a[^>]*href="/partnering/[^"]*"[^>]*>(.*?)</a>', b, re.S) or [None, ""])[1])
            out.append(job(jid=jid, title=title, company=org, location="Germany", published=date,
                           url="https://euraxess.ec.europa.eu" + m.group(1), source="EURAXESS",
                           ctype="University / research"))
    log(f"  EURAXESS: {len(out)}")
    return out


# ---------- Arbeitnow (startups, English-language jobs in Germany) --------------------------
def arbeitnow(pages=5, log=print):
    out = []
    for p in range(1, pages + 1):
        r = http("GET", f"https://www.arbeitnow.com/api/job-board-api?page={p}", timeout=40)
        if r is None or r.status_code != 200:
            break
        try:
            data = r.json().get("data", [])
        except ValueError:
            break
        for it in data:
            date = iso_date(it.get("created_at"))
            if not title_ok(it.get("title")) or not _fresh(date):
                continue
            out.append(job(jid=f"an-{it.get('slug')}", title=it.get("title"), company=it.get("company_name", ""),
                           location=it.get("location", ""), published=date, url=it.get("url", ""),
                           desc=strip_html(it.get("description", "")), source="Arbeitnow", ctype="Startup / SME"))
        if not data:
            break
        time.sleep(1.0)
    log(f"  Arbeitnow: {len(out)}")
    return out


# ---------- helpers ---------------------------------------------------------------------------
MONTHS = {m: i + 1 for i, m in enumerate("january february march april may june july august september "
                                         "october november december".split())}
MON3 = {m[:3]: i for m, i in MONTHS.items()}


def _dmy(s):
    m = re.match(r"(\d{1,2}) (\w+) (\d{4})", s or "")
    if not m or m.group(2).lower() not in MONTHS:
        return ""
    return f"{m.group(3)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _rfc822(s):
    m = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", s or "")
    if not m or m.group(2).lower() not in MON3:
        return ""
    return f"{m.group(3)}-{MON3[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _ld_jobposting(txt):
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', txt, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for x in (d if isinstance(d, list) else d.get("@graph", [d]) if isinstance(d, dict) else []):
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
    return None


def _org(d):
    o = d.get("hiringOrganization") or {}
    return o.get("name", "") if isinstance(o, dict) else str(o)


def _place(d):
    loc = d.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    a = (loc.get("address") or {}) if isinstance(loc, dict) else {}
    if isinstance(a, str):
        return a
    return ", ".join(x for x in (a.get("postalCode"), a.get("addressLocality"), a.get("addressCountry"))
                     if isinstance(x, str) and x)


# ---------- run everything ----------------------------------------------------------------------
def crawl_all(cfg, seen, log=print, budget_min=25):
    """Run every board in parallel threads (each stops fetching after budget_min); returns (jobs, stats)."""
    from concurrent.futures import ThreadPoolExecutor
    DEADLINE[0] = time.time() + budget_min * 60
    kw = cfg.get("board_keywords", [])
    rk = cfg.get("research_keywords", kw)
    tasks = {
        "LinkedIn DE": lambda: linkedin(kw, "Germany", seen=seen, log=log),
        "LinkedIn Bengaluru": lambda: linkedin(cfg.get("linkedin_en_keywords", []), "Bengaluru, Karnataka, India",
                                               pages=2, seen=seen, log=log, source="LinkedIn (Bengaluru)"),
        "LinkedIn France": lambda: linkedin(cfg.get("linkedin_en_keywords", []), "France", pages=2, seen=seen,
                                            log=log, source="LinkedIn (France)"),
        "StepStone": lambda: stepstone(kw, log=log),
        "XING": lambda: xing(kw, seen=seen, log=log),
        "Jobware": lambda: jobware(kw, log=log),
        "academics.de": lambda: academics(rk, seen=seen, log=log),
        "service.bund.de": lambda: bund(rk, log=log),
        "Fraunhofer/DLR": lambda: sf_rss(rk, log=log),
        "EURAXESS": lambda: euraxess(cfg.get("euraxess_keywords", []), log=log),
        "Arbeitnow": lambda: arbeitnow(log=log),
    }
    jobs, stats = [], {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futs = {name: ex.submit(fn) for name, fn in tasks.items()}
        for name, f in futs.items():
            try:
                res = f.result()
            except Exception as e:  # noqa: BLE001 - one broken board must not stop the crawl
                stats[name] = f"error {type(e).__name__}: {str(e)[:80]}"
                continue
            stats[name] = len(res)
            jobs += res
    return jobs, stats
