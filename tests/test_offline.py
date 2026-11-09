"""Offline tests: fake HTTP responses for each adapter. Run: python -m pytest -q tests"""
import datetime as dt, json, types
import crawler.common as C, crawler.ats as A, crawler.ba as B, crawler.generic as G

T = C.TODAY.isoformat(); OLD = (C.TODAY - dt.timedelta(days=20)).isoformat()
DESC = "Entwicklung von Wechselrichtern, MATLAB/Simulink, HiL-Tests, Leistungselektronik."

class R:
    def __init__(s, body, status=200, ctype="text/html", url="https://x"):
        s.status_code, s.url = status, url
        s.headers = {"content-type": ctype}
        if isinstance(body, (dict, list)): s._j, s.text = body, json.dumps(body)
        else: s._j, s.text = None, body
        s.content = s.text.encode()
    def json(s): return s._j

def patch(monkeypatch, routes):
    def fake(method, url, **kw):
        for k, v in routes.items():
            if k in url: return v(kw) if callable(v) else v
        return R("", 404)
    for mod in (C, A, B, G):
        monkeypatch.setattr(mod, "http", fake, raising=False)
    gj = lambda url, **kw: (lambda r: r.json() if r and r.status_code == 200 else None)(fake("GET", url, **kw))
    for mod in (C, A, B, G):
        monkeypatch.setattr(mod, "get_json", gj, raising=False)

ROW = lambda **k: {"name": "Acme", "type": "Startup / SME", **k}

def test_filters():
    assert C.is_germany("68309 Mannheim") and C.is_germany("Kaiserslautern") and C.is_germany("Munich, BY, DE")
    assert not C.is_germany("Paris, Île-de-France") and not C.is_germany("Austin, TX 78701")
    assert C.relevant("Ingenieur Leistungselektronik (m/w/d)", "")
    assert not C.relevant("Senior Engineer Power Electronics", DESC)
    assert not C.relevant("Werkstudent Simulink", DESC)
    assert C.relevant("Wissenschaftlicher Mitarbeiter (m/w/d)", DESC)
    assert not C.relevant("Buchhalter (m/w/d)", DESC)

def test_greenhouse(monkeypatch):
    patch(monkeypatch, {"greenhouse.io": R({"jobs": [
        {"id": 1, "title": "Power Electronics Engineer", "location": {"name": "Munich, Germany"}, "content": DESC, "first_published": T, "absolute_url": "u1"},
        {"id": 2, "title": "Power Electronics Engineer", "location": {"name": "London, UK"}, "content": DESC, "absolute_url": "u2"},
        {"id": 3, "title": "Office Manager", "location": {"name": "Berlin"}, "content": DESC, "absolute_url": "u3"}]})})
    out = A.greenhouse(ROW(ats_id="acme"))
    assert [j["jobId"] for j in out] == ["gh-1"] and out[0]["publishedAt"] == T

def test_lever_ashby(monkeypatch):
    patch(monkeypatch, {"api.eu.lever.co": R([{"id": "a", "text": "Systems Engineer e-drive", "categories": {"location": "Stuttgart"},
                         "descriptionPlain": DESC, "createdAt": 1.7e12, "hostedUrl": "h"}]),
                        "ashbyhq": R({"jobs": [{"id": "b", "title": "Embedded Engineer Battery", "location": "Munich",
                         "descriptionPlain": "BMS, battery, Simulink", "publishedAt": T, "jobUrl": "j"}]})})
    assert A.lever(ROW(ats_id="eu:acme"))[0]["jobId"] == "lv-a"
    assert A.ashby(ROW(ats_id="acme"))[0]["jobUrl"] == "j"

def test_smartrecruiters(monkeypatch):
    patch(monkeypatch, {"/postings/9": R({"postingUrl": "p9", "jobAd": {"sections": {"jobDescription": {"text": DESC}}}}),
                        "/postings": R({"content": [
        {"id": "9", "name": "HiL Test Engineer (m/w/d)", "location": {"city": "Regensburg", "country": "de"}, "releasedDate": T, "ref": "https://api/x/postings/9"},
        {"id": "8", "name": "HiL Test Engineer (m/w/d)", "location": {"city": "Regensburg", "country": "de"}, "releasedDate": OLD}]})})
    out = A.smartrecruiters(ROW(ats_id="Acme"))
    assert [j["jobId"] for j in out] == ["sr-9"]

def test_personio(monkeypatch):
    xml = f"""<?xml version="1.0"?><workzag-jobs><position><id>77</id><office>Kaiserslautern</office><name>Entwicklungsingenieur Antriebstechnik (m/w/d)</name>
    <createdAt>{T}T08:00:00+00:00</createdAt><jobDescriptions><jobDescription><name>Aufgaben</name><value>&lt;p&gt;{DESC}&lt;/p&gt;</value></jobDescription></jobDescriptions></position></workzag-jobs>"""
    patch(monkeypatch, {"jobs.personio.de": R(xml)})
    out = A.personio(ROW(ats_id="acme"))
    assert out[0]["jobId"] == "pe-acme-77" and "Simulink" in out[0]["jobDescription"]

def test_workday(monkeypatch):
    def jobs(kw):
        if kw["json"]["offset"] > 0 or kw["json"]["searchText"] != "Simulink": return R({"jobPostings": []})
        return R({"jobPostings": [{"title": "Function Developer Simulink", "externalPath": "/job/Stuttgart/Fn_R1", "postedOn": "Posted 3 Days Ago", "locationsText": "Stuttgart"},
                                  {"title": "Function Developer Simulink", "externalPath": "/job/x/Old_R2", "postedOn": "Posted 30+ Days Ago"}]})
    patch(monkeypatch, {"/job/Stuttgart/Fn_R1": R({"jobPostingInfo": {"jobDescription": DESC, "location": "Stuttgart, Germany", "jobReqId": "R1", "externalUrl": "ext"}}),
                        "/jobs": jobs})
    out = A.workday(ROW(ats_id="acme|wd3|careers"))
    assert [j["jobId"] for j in out] == ["wd-acme-R1"]
    assert out[0]["publishedAt"] == (C.TODAY - dt.timedelta(days=3)).isoformat()

def test_generic(monkeypatch):
    page = '<a href="/jobs/42">Ingenieur Leistungselektronik (m/w/d)</a><a href="/about">Über uns</a><a href="/jobs/43">Werkstudent (m/w/d)</a>'
    patch(monkeypatch, {"/karriere": R(page), "/jobs/42": R(f"<main>{DESC}</main>")})
    out = G.listing(ROW(careers_url="https://acme.de/karriere"))
    assert [j["jobTitle"] for j in out] == ["Ingenieur Leistungselektronik (m/w/d)"]
    G.enrich(out[0]); assert "Simulink" in out[0]["jobDescription"]
    ld = '<script type="application/ld+json">{"@type":"JobPosting","title":"Research Engineer e-drive","datePosted":"%s","url":"https://u","description":"%s","jobLocation":{"address":{"addressLocality":"Aachen","addressCountry":"DE"}}}</script>' % (T, DESC)
    patch(monkeypatch, {"/karriere": R(ld)})
    out = G.listing(ROW(careers_url="https://acme.de/karriere"))
    assert out[0]["publishedAt"] == T and out[0]["location"].endswith("DE")

def test_ba(monkeypatch):
    patch(monkeypatch, {"/pc/v6/jobs": R({"maxErgebnisse": 2, "ergebnisliste": [
        {"referenznummer": "10000-1", "stellenangebotsTitel": "Entwicklungsingenieur Leistungselektronik (m/w/d)", "firma": "Klein GmbH",
         "stellenlokationen": [{"adresse": {"plz": "67655", "ort": "Kaiserslautern"}}], "datumErsteVeroeffentlichung": T},
        {"referenznummer": "10000-2", "stellenangebotsTitel": "Lagerhelfer", "firma": "Logistik AG", "datumErsteVeroeffentlichung": T}]}),
        "/pc/v4/jobdetails/": R({"stellenangebotsBeschreibung": DESC, "externeUrl": "https://www.klein.de/jobs/1"})})
    jobs, emps = B.crawl(["Leistungselektronik"], log=lambda *a: None)
    assert [j["jobId"] for j in jobs] == ["ba-10000-1"] and emps["Klein GmbH"] == "klein.de"
    assert jobs[0]["location"] == "67655 Kaiserslautern" and jobs[0]["publishedAt"] == T

def test_ats_detection():
    import detect_ats as D
    assert D.match_ats('src="https://boards.greenhouse.io/embed/job_board/js?for=isar"') == ("greenhouse", "isar")
    assert D.match_ats("https://jobs.eu.lever.co/helsing/123") == ("lever", "eu:helsing")
    assert D.match_ats("https://zf.wd3.myworkdayjobs.com/de-DE/careers/job/x") == ("workday", "zf|wd3|careers")
    assert D.match_ats("https://instagrid.jobs.personio.de/job/1") == ("personio", "instagrid")
    assert D.match_ats("https://jobs.smartrecruiters.com/BoschGroup/7440") == ("smartrecruiters", "BoschGroup")


def test_successfactors(monkeypatch):
    sm = ('<urlset><url><loc>https://jobs.x.com/job/Schweinfurt-Ingenieur-Leistungselektronik-%28mwd%29-BY-97421/1450000001/</loc>'
          '<lastmod>2026-11-07</lastmod></url><url><loc>https://jobs.x.com/job/Pune-Engineer-Inverter-MH-411/1450000002/</loc></url>'
          '<url><loc>https://jobs.x.com/job/Passau-Buchhalter-%28mwd%29-94032/1450000003/</loc></url></urlset>')
    stamp = C.TODAY.strftime("%a %b %d 02:00:00 UTC %Y")
    de = (f'<span itemprop="title">Ingenieur Leistungselektronik (m/w/d)</span><meta itemprop="datePosted" content="{stamp}">'
          '<meta itemprop="addressCountry" content="DE"><meta itemprop="addressLocality" content="Schweinfurt">'
          f'<span class="jobdescription">{DESC}</span></div>')
    inn = de.replace('content="DE"', 'content="IN"')
    patch(monkeypatch, {"sitemap.xml": R(sm), "1450000001": R(de), "1450000002": R(inn)})
    out = A.successfactors(ROW(ats_id="jobs.x.com"))
    assert [j["jobId"] for j in out] == ["sf-jobs.x.com-1450000001"] and out[0]["publishedAt"] == T


def test_eightfold(monkeypatch):
    ts = int(dt.datetime.combine(C.TODAY, dt.time(12)).timestamp())
    body = {"data": {"positions": [
        {"id": 1, "name": "Application Engineer Power Electronics (f/m/div)", "standardizedLocations": ["Munich, BY, DE"],
         "postedTs": ts, "positionUrl": "/careers/job/1"},
        {"id": 2, "name": "Application Engineer Power Electronics", "standardizedLocations": ["Villach, Carinthia, AT"],
         "postedTs": ts},
        {"id": 3, "name": "Senior Engineer R&D", "standardizedLocations": ["Warstein, NRW, DE"], "postedTs": ts}]}}
    patch(monkeypatch, {"pcsx/search": R(body)})
    out = A.eightfold(ROW(ats_id="jobs.y.com|y.com"), terms=["x"])
    assert [j["jobId"] for j in out] == ["ef-y.com-1"]


def test_beesite(monkeypatch):
    body = {"SearchResult": {"SearchResultItems": [
        {"MatchedObjectId": "9", "MatchedObjectDescriptor": {"ID": "9", "PositionTitle": "Entwicklungsingenieur E-Antrieb (w/m/d)",
         "PositionURI": "https://jobs.mb/9", "PositionLocation": [{"CityName": "Sindelfingen", "CountryName": "Deutschland", "CountryCode": "DE"}],
         "PublicationStartDate": T, "UserArea": {"Details": {"JobDescription": DESC}}}}]}}
    body["SearchResult"]["SearchResultItems"][0]["MatchedObjectDescriptor"]["PositionFormattedDescription"] = [{"Content": DESC}]
    patch(monkeypatch, {"api.mb": R(body)})
    out = A.beesite(ROW(ats_id="https://api.mb/search"))
    assert out and out[0]["jobId"] == "bs-9" and out[0]["publishedAt"] == T


def test_rotation_and_db_merge():
    import crawl, build_db
    a = {"name": "Acme", "tier": "A"}; b = {"name": "Beta GmbH", "tier": "B"}
    assert all(crawl.due_today(a, d) for d in range(7))
    assert sum(crawl.due_today(b, d) for d in range(7)) == 1          # tier B: exactly once a week
    rows = build_db.merge([
        [{"name": "Beispiel Leistungselektronik GmbH", "domain": "beispiel.de", "industry": "", "city": "", "sources": {"osm"}}],
        [{"name": "Beispiel Leistungselektronik", "domain": "www.beispiel.de".removeprefix("www."), "industry": "electrical equipment",
          "city": "Ulm", "sources": {"wikidata"}}],
        [{"name": "Musterbäckerei GmbH", "domain": "", "industry": "", "city": "Köln", "lei": "X", "sources": {"gleif"}}],
        [{"name": "Kanzlei Muster", "domain": "kanzlei-muster.de", "industry": "law firm", "city": "", "sources": {"osm"}}]])
    by = {r["name"]: r for r in rows}
    m = by["Beispiel Leistungselektronik GmbH"]
    assert m["sources"] == {"osm", "wikidata"} and m["tier"] == "A" and m["city"] == "Ulm"
    assert by["Musterbäckerei GmbH"]["tier"] == "C" and by["Kanzlei Muster"]["tier"] == "B"


def test_contacts():
    t = ("Ihre Ansprechpartnerin: Frau Anna Becker, Tel. +49 (0) 621 123456-78. Bewerbung bitte an "
         "karriere@muster-antriebe.de. Datenschutz: datenschutz@muster-antriebe.de")
    c = C.contacts(t, ["https://www.arbeitsagentur.de/x", "https://muster-antriebe.de/jobs/1"])
    assert c["applyEmail"] == "karriere@muster-antriebe.de" and c["applyMethod"] == "email"
    assert c["contactPerson"] == "Frau Anna Becker" and c["applyUrl"] == "https://muster-antriebe.de/jobs/1"
    assert "621" in c["contactPhone"]
    assert C.contacts("Bewerben Sie sich online.")["applyMethod"] == "see posting"
