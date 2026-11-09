"""Shared helpers: HTTP session, text cleanup, relevance + Germany filters, job record."""
import datetime as dt
import html
import json
import re
import time

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 job-crawler/1.0 (personal job search)")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
TODAY = dt.date.today()


def http(method, url, retries=2, timeout=25, **kw):
    """GET/POST with small retry; returns Response or None (never raises)."""
    for attempt in range(retries + 1):
        try:
            r = S.request(method, url, timeout=timeout, **kw)
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1)); continue
            return r
        except requests.RequestException:
            if attempt < retries:
                time.sleep(2 * (attempt + 1)); continue
    return None


def get_json(url, **kw):
    r = http("GET", url, **kw)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


# ---------- filters -------------------------------------------------------
X = lambda p: re.compile(p, re.I)

# Titles we never want (mirrors the tracker rules). Doktorand/PhD are KEPT on purpose:
# PhD-track WiMi posts are in scope and get flagged later.
TITLE_OUT = X(r"\b(senior|sr\.|lead|principal|head of|leiter|leitung|director|manager|werkstudent|working student|"
              r"praktikum|praktikant|intern|internship|thesis|abschlussarbeit|masterarbeit|bachelorarbeit|"
              r"ausbildung|azubi|duales studium|sales|vertrieb|account|recruiter|buchhalt|professur|professor|"
              r"postdoc|post-doc|studentische|hilfskraft|hiwi|mechaniker|monteur|elektriker|servicetechniker)\b")
# Broad "this is an engineering/research role" test on the title.
TITLE_ROLE = X(r"ingenieur|engineer|entwickl|developer|wissenschaftl|research|forsch|simulation|validier|"
               r"test|system|applikation|elektro|electrical|power|antrieb|drive|batter|hochvolt|embedded|"
               r"regelung|control|mechatron|mobil|absolvent|graduate|junior|doktorand|promotion|phd|"
               r"funktion|function|hardware|software|e/e|powertrain|inverter|umrichter|motor")
# Core profile terms (title or description must hit one).
CORE = X(r"simulink|simscape|stateflow|\bmatlab\b|model[- ]based|modellbasiert|\bmbd\b|"
         r"\bhil\b|\bmil\b|\bsil\b|hardware[- ]in[- ]the[- ]loop|\bdspace\b|canoe|capl|vector|"
         r"power electronic|leistungselektronik|inverter|wechselrichter|umrichter|converter|dc[/-]dc|"
         r"on-?board charger|\bobc\b|e[- ]?motor|elektrische maschine|electric machine|e[- ]?antrieb|"
         r"e[- ]?drive|electric drive|antriebstechnik|traktion|traction|regelungstechnik|control engineer|"
         r"control system|motor control|\bbms\b|battery|batterie|hochvolt|high[- ]voltage|ladetechnik|"
         r"charging|fahrzeugtechnik|nutzfahrzeug|elektromobil|e-mobility|powertrain|antriebsstrang|"
         r"elektrotechnik|electrical engineering|elektronik|electronics|embedded|mechatron|automatisierung|"
         r"steuerungstechnik|energietechnik|antriebssystem|elektrifizierung|electrification")
# Engineering titles that are kept even when the posting text is too short to show a profile term.
ENG_TITLE = X(r"ingenieur|engineer|entwicklung|entwickler|wissenschaftliche|absolvent|graduate|trainee|"
              r"berufseinsteiger|junior")

DE_CITIES = ("aachen augsburg bamberg bayreuth berlin bielefeld bochum bonn braunschweig bremen chemnitz "
             "cottbus darmstadt dortmund dresden duisburg düsseldorf duesseldorf erlangen essen esslingen "
             "flensburg frankfurt freiburg friedrichshafen fulda gaimersheim garching gifhorn göttingen "
             "hamburg hannover heidelberg heilbronn hildesheim ilmenau ingolstadt jena kaiserslautern "
             "karlsruhe kassel kiel koblenz köln cologne konstanz krefeld landshut leipzig leverkusen "
             "lippstadt ludwigsburg lübeck magdeburg mainz mannheim munich münchen muenchen münster "
             "neckarsulm nuremberg nürnberg oberkochen offenburg oldenburg osnabrück paderborn passau "
             "pforzheim potsdam regensburg reutlingen rosenheim rostock rüsselsheim saarbrücken "
             "salzgitter schweinfurt siegen sindelfingen stuttgart tübingen ulm weingarten weissach "
             "wiesbaden wolfsburg würzburg wuppertal zwickau böblingen herzogenaurach bühl").split()
GERMANY = X(r"germany|deutschland|\bD-\d{5}|" + "|".join(map(re.escape, DE_CITIES)))
DE_CODE = re.compile(r"(,\s*|\b)DE(U)?\b")          # case-sensitive "…, DE"
PLZ = re.compile(r"(^|[\s,(])\d{5}\s+[A-ZÄÖÜ][a-zäöüß]")   # "68309 Mannheim"


def is_germany(location, country_code=None):
    if country_code:
        return country_code.upper() in ("DE", "DEU")
    if not location:
        return False
    return bool(GERMANY.search(location) or DE_CODE.search(location) or PLZ.search(location))


# Compound German words and student formats that the \b-anchored TITLE_OUT misses
# (Schülerpraktikum, Pflichtpraktikant, Auszubildender, Duales/Kooperatives Studium, Masterand, apprentice).
TITLE_OUT2 = X(r"kfz|elektrofachkraft|\bmeister\b|^techniker|praktik|ausbildung|auszubild|azubi|studium\b|dual(es|er)? stud|kooperativ|masterand|"
               r"apprentic|schüler|\bb\.?\s?eng\.|\bb\.?\s?sc\.|abschlussarbeit|"
               r"\b(?<!phd )(?<!doctoral )student(in)?\b")


def relevant(title, desc=""):
    """Cheap pre-filter. Real scoring happens later in score.py + manual reading."""
    if not title or TITLE_OUT.search(title) or TITLE_OUT2.search(title):
        return False
    if not TITLE_ROLE.search(title):
        return False
    if CORE.search(title + " " + (desc or "")):
        return True
    # short or missing text (list pages, truncated ads): keep engineering titles, the pre-score ranks them
    return len(desc or "") < 400 and bool(ENG_TITLE.search(title))


def title_ok(title):
    """Title-only check, for list endpoints without descriptions (detail fetched later)."""
    return bool(title) and not TITLE_OUT.search(title) and not TITLE_OUT2.search(title) \
        and bool(TITLE_ROLE.search(title))


def iso_date(v):
    """Accept ISO strings, epoch ms/s, or None -> 'YYYY-MM-DD' or ''."""
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):
        if v > 1e11:
            v /= 1000
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(v))
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", str(v))
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def age_days(date_str):
    try:
        return (TODAY - dt.date.fromisoformat(date_str)).days
    except (TypeError, ValueError):
        return None


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_SKIP = re.compile(r"noreply|no-reply|datenschutz|privacy|dsb@|webmaster|example\.|\.(png|jpg|gif)$", re.I)
PHONE = re.compile(r"(?:\+49|0049|\b0)[\s/().-]*\d{2,5}[\s/().-]*\d{2,}(?:[\s/.-]*\d{1,})*")
PERSON = re.compile(r"(?:Ansprechpartner(?:in)?|Kontakt(?:person)?|Ihre? Ansprechperson|contact person|Fragen\w*[^.:]{0,40}?)"
                    r"\s*[:\-–]?\s*((?:Frau|Herr|Fr\.|Hr\.|Ms\.?|Mr\.?|Dr\.?|Prof\.?)\s+(?:Dr\.\s+)?"
                    r"[A-ZÄÖÜ][\wäöüß-]+(?:\s+[A-ZÄÖÜ][\wäöüß-]+){0,2})")


def contacts(text, urls=()):
    """How to apply when there is no portal: e-mail addresses, phone, contact person, external apply link.
    Only what the posting itself publishes; nothing is looked up elsewhere."""
    text = text or ""
    emails = []
    for e in EMAIL.findall(text):
        e = e.strip(".").lower()
        if not EMAIL_SKIP.search(e) and e not in emails:
            emails.append(e)
    phones = [re.sub(r"\s+", " ", p).strip(" -/") for p in PHONE.findall(text)]
    phones = [p for p in dict.fromkeys(phones) if sum(c.isdigit() for c in p) >= 8][:2]
    m = PERSON.search(text)
    apply_url = next((u for u in urls if u and "arbeitsagentur.de" not in u), "")
    out = {}
    if emails:
        out["applyEmail"] = ", ".join(emails[:3])
    if phones:
        out["contactPhone"] = ", ".join(phones)
    if m:
        out["contactPerson"] = m.group(1).strip()
    if apply_url:
        out["applyUrl"] = apply_url
    out["applyMethod"] = "email" if emails else ("external link" if apply_url else "see posting")
    return out


def job(*, jid, title, company, location, url, published="", desc="", source, ctype="", extra=None):
    """Record in the same field names score.py/extract.py already use (LinkedIn schema)."""
    d = {
        "jobId": jid, "jobTitle": (title or "").strip(), "companyName": company, "location": location or "",
        "publishedAt": published, "jobDescription": desc or "", "jobUrl": url, "source": source,
        "companyType": ctype,
    }
    if extra:
        d.update(extra)
    return d


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
