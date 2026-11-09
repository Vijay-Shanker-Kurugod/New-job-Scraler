"""Fallback for custom career pages (universities, institutes, Mittelstand websites).

1. schema.org JobPosting JSON-LD on the page (gives a real datePosted), else
2. links whose text looks like a job ad, e.g. "Ingenieur Leistungselektronik (m/w/d)".
   These have no publish date; crawl.py dates them by first-seen.
"""
import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

from .common import http, is_germany, iso_date, job, strip_html, title_ok

JOBLIKE = re.compile(r"\((m|w|d|f|x|all genders?|gn)\s*[/|,]\s*(m|w|d|f|x)[^)]*\)|\b(m/w/d|w/m/d|m/f/d|f/m/d|"
                     r"d/m/w|all genders)\b|wissenschaftliche[rn]? mitarbeiter|research (assistant|associate|engineer)|"
                     r"doktorand|ingenieur|engineer", re.I)
LDJSON = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
ANCHOR = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.S | re.I)


def _ld_postings(html_text):
    out = []
    for blob in LDJSON.findall(html_text):
        try:
            data = json.loads(blob.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                if "@graph" in d:
                    stack += d["@graph"]
                t = d.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    out.append(d)
            elif isinstance(d, list):
                stack += d
    return out


def _ld_location(p):
    locs = p.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    parts = []
    for l in locs:
        a = (l or {}).get("address") or {}
        if isinstance(a, dict):
            c = a.get("addressCountry")
            c = c.get("name") if isinstance(c, dict) else c
            parts.append(", ".join(filter(None, [a.get("postalCode"), a.get("addressLocality"), c])))
        elif isinstance(a, str):
            parts.append(a)
    return "; ".join(filter(None, parts))


def _jid(url):
    return "gn-" + hashlib.sha1(url.encode()).hexdigest()[:14]


def listing(row):
    """Return title-ok candidates from the career page (desc may be empty)."""
    url = row.get("careers_url")
    if not url:
        return []
    r = http("GET", url)
    if r is None or r.status_code != 200:
        raise RuntimeError(f"career page HTTP {getattr(r, 'status_code', 'no response')}")
    return parse(row, r.text, url)


def parse(row, page, url):
    """Job candidates from a career page's HTML (static or browser-rendered)."""
    out = []
    for p in _ld_postings(page):
        u = p.get("url") or url
        loc = _ld_location(p)
        if loc and not is_germany(loc):
            continue
        out.append(job(jid=_jid(u), title=p.get("title", ""), company=row["name"], location=loc or "Deutschland",
                       url=u, published=iso_date(p.get("datePosted")), desc=strip_html(p.get("description")),
                       source=f"Career site ({row['name']})", ctype=row.get("type", "")))
    if out:
        return [j for j in out if title_ok(j["jobTitle"])]
    host = urlparse(url).netloc
    seen = set()
    for href, text in ANCHOR.findall(page):
        title = strip_html(text)
        if not (8 < len(title) < 180) or not JOBLIKE.search(title) or not title_ok(title):
            continue
        u = urljoin(url, href)
        if u in seen or u.lower().endswith((".jpg", ".png")):
            continue
        seen.add(u)
        out.append(job(jid=_jid(u), title=title, company=row["name"], location="Deutschland", url=u,
                       published="", desc="", source=f"Career site ({row['name']})",
                       ctype=row.get("type", ""), extra={"sameHost": urlparse(u).netloc == host}))
    return out


def enrich(rec):
    """Fetch a single ad page: description and (if present) datePosted / PDF stays empty."""
    if rec.get("jobDescription"):
        return rec
    r = http("GET", rec["jobUrl"])
    if r is None or r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        return rec
    lds = _ld_postings(r.text)
    if lds:
        p = lds[0]
        rec["jobDescription"] = strip_html(p.get("description"))
        rec["publishedAt"] = rec["publishedAt"] or iso_date(p.get("datePosted"))
        loc = _ld_location(p)
        if loc:
            rec["location"] = loc
    else:
        body = re.search(r"(?is)<(main|article)\b.*?</\1>", r.text)
        rec["jobDescription"] = strip_html(body.group(0) if body else r.text)[:8000]
    return rec
