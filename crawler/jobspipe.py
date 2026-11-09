"""Indeed jobs in Germany through the JobsPipe REST API (the board itself forbids crawling).

Runs only when the GitHub repository secret JOBSPIPE_API_KEY is set. One search a day, at most
JOBSPIPE_LIMIT jobs (default 25); one credit buys one job, and a job paid for once is free
when it comes back. Logs a warning when fewer than 30 credits are left.
"""
import os

from .common import http, iso_date, job, relevant, strip_html

API = "https://api.jobspipe.dev/v1/jobs/search"
TITLES = ["Leistungselektronik", "Power Electronics", "Inverter", "Wechselrichter", "Umrichter", "E-Antrieb",
          "Elektrische Antriebe", "Electric Drive", "Elektromotor", "E-Motor", "Antriebsentwicklung",
          "Funktionsentwicklung", "Regelungstechnik", "Control Engineer", "Model-Based", "Simulink", "HiL",
          "Hardware-in-the-Loop", "Validierung", "Validation Engineer", "Testingenieur", "Test Engineer",
          "Systemingenieur", "System Engineer", "Embedded", "BMS", "Batterie", "Battery", "Hochvolt",
          "Ladetechnik", "Charging", "Applikationsingenieur", "Application Engineer", "Entwicklungsingenieur",
          "Development Engineer", "Simulation", "Absolvent", "Graduate", "Trainee", "Junior Ingenieur",
          "Berufseinsteiger", "Wissenschaftlicher Mitarbeiter", "Research Engineer"]
TITLE_NOT = ["Senior", "Lead", "Principal", "Head of", "Leiter", "Werkstudent", "Praktikum", "Praktikant",
             "Abschlussarbeit", "Masterarbeit", "Ausbildung", "Duales Studium", "Meister", "Techniker", "Sales",
             "Vertrieb"]


def crawl(log=print):
    token = os.environ.get("JOBSPIPE_API_KEY", "").strip()
    if not token:
        return [], {"jobspipe": "skipped (no JOBSPIPE_API_KEY repository secret)"}
    body = {"job_country_code_or": ["DE"], "source_or": ["indeed"], "posted_at_max_age_days": 2,
            "job_title_or": TITLES, "job_title_not": TITLE_NOT, "detail": "full",
            "limit": int(os.environ.get("JOBSPIPE_LIMIT", "25"))}
    r = http("POST", API, json=body, timeout=60, retries=1,
             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    if r is None or r.status_code != 200:
        code = getattr(r, "status_code", "none")
        return [], {"jobspipe": f"http {code}" + (" (out of credits)" if code == 402 else "")}
    d = r.json() or {}
    meta = d.get("metadata") or {}
    out = []
    for j in d.get("data") or []:
        desc = strip_html(j.get("description") or "")
        title = j.get("job_title") or ""
        if desc and not relevant(title, desc):
            continue
        out.append(job(jid=f"jp-{j.get('id')}", title=title, company=j.get("company") or "",
                       location=j.get("location") or "Deutschland", url=j.get("url") or "",
                       published=iso_date(j.get("date_posted")), desc=desc,
                       source="Indeed (via JobsPipe)"))
    left = meta.get("credits_remaining")
    log(f"jobspipe: {len(d.get('data') or [])} returned, {len(out)} relevant, credits left {left}")
    if isinstance(left, (int, float)) and left < 30:
        log("jobspipe: fewer than 30 credits left – top up or remove the repository secret")
    return out, {"jobspipe": f"{len(out)} relevant, credits left {left}"}
