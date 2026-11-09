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
    (tmp_path / "companies.csv").write_text("name,domain,type,ats,ats_id,careers_url,source,status,checked\nA,a.de,,,,x,,active,\nB,b.de,,greenhouse,b,,,active,\n")
    T = C.TODAY.isoformat(); OLD = (C.TODAY - dt.timedelta(days=30)).isoformat()
    out = run(tmp_path, monkeypatch, {"A": [J("gn-1")], "B": [J("gh-1", T), J("gh-2", OLD)]})
    assert [i["jobId"] for i in out["items"]] == ["gh-1"]            # undated A = baseline, old dropped
    assert out["stats"]["baseline_recorded"] == 1 and out["stats"]["too_old"] == 1
    out = run(tmp_path, monkeypatch, {"A": [J("gn-1"), J("gn-2")], "B": [J("gh-1", T)]})
    assert [i["jobId"] for i in out["items"]] == ["gh-1", "gn-2"]   # same day: appended, no duplicates
    assert out["stats"]["new"] == 1
    assert out["items"][1]["dateSource"] == "first_seen"
