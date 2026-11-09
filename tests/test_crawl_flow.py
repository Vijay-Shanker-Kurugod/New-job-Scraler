"""crawl.py bookkeeping: new vs seen, too-old, first-seen baseline for undated sites."""
import json, os, shutil, sys, datetime as dt
import crawl, crawler.common as C

def run(tmp, monkeypatch, results):
    monkeypatch.setattr(crawl, "ROOT", str(tmp))
    monkeypatch.setattr(crawl, "crawl_company", lambda row, seen: (row, results.get(row["name"], []), None))
    monkeypatch.setattr(sys, "argv", ["crawl.py", "--no-ba", "--no-boards"])
    crawl.main()
    return json.load(open(tmp / "data" / "latest.json"))

def J(i, pub=""):
    return C.job(jid=i, title="t", company="c", location="Berlin", url="u", published=pub, source="Career site (c)")

def test_flow(tmp_path, monkeypatch):
    (tmp_path / "companies.csv").write_text("name,domain,type,ats,ats_id,careers_url,source,status,checked,tier\nA,a.de,,,,x,,active,,A\nB,b.de,,greenhouse,b,,,active,,A\nW,w.de,,,,x,,active,,B\n")
    T = C.TODAY.isoformat(); OLD = (C.TODAY - dt.timedelta(days=40)).isoformat()
    D20 = (C.TODAY - dt.timedelta(days=20)).isoformat()
    monkeypatch.setattr(crawl, "due_today", lambda r, d=None: True)
    out = run(tmp_path, monkeypatch, {"A": [J("gn-1")], "B": [J("gh-1", T), J("gh-2", OLD), J("gh-3", D20)],
                                      "W": [J("wk-1")]})
    ids = sorted(i["jobId"] for i in out["items"])
    # first crawl: tier-A undated job reported once (flagged), dated jobs up to 30 days, tier-B undated baselined
    assert ids == ["gh-1", "gh-3", "gn-1"]
    assert {i["jobId"]: i["dateSource"] for i in out["items"]}["gn-1"] == "first_crawl"
    assert out["stats"]["baseline_recorded"] == 1 and out["stats"]["too_old"] == 1
    out = run(tmp_path, monkeypatch, {"A": [J("gn-1"), J("gn-2")], "B": [J("gh-1", T)], "W": [J("wk-1")]})
    assert sorted(i["jobId"] for i in out["items"]) == ["gh-1", "gh-3", "gn-1", "gn-2"]   # same day: appended
    assert out["stats"]["new"] == 1
    assert {i["jobId"]: i["dateSource"] for i in out["items"]}["gn-2"] == "first_seen"
    assert all("preScore" in i for i in out["items"])
