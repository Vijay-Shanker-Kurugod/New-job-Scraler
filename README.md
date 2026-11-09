# job-crawler

A free daily job crawler for an engineering job search in Germany: e-mobility, power electronics, e-drives, HiL/MBD, plus university and research posts.

## What runs
| Workflow | When | What it does |
|---|---|---|
| `daily-crawl` | every day 05:15 UTC | Fetches the last 7 days from the **Bundesagentur für Arbeit API** (all ~45 keywords in `config.json`) and checks **every employer in `companies.csv`**. Writes only jobs that are new since earlier runs to `data/new/<date>.json` and `data/latest.json`. |
| `grow-employer-list` | Sundays 02:00 UTC | Adds every employer the Arbeitsagentur crawl found (`data/ba_employers.csv`) to `companies.csv` and detects which career-page system each employer uses. |

Claude's daily scan (07:47 Berlin) reads `data/latest.json`, scores the jobs and updates the tracker.

## Full sweep (manual)
`full-sweep` (sweep.yml): looks up websites for Arbeitsagentur employers, detects career systems for new employers, then re-reads every career page and lists every relevant job from the last 7 days (`data/sweep/<date>.json`, per-site report in `<date>-companies.json`).

## Browser pass (since 31 Oct 2026)
Generic career pages that show no jobs in plain HTML are opened a second time in headless Chromium (Playwright, 6 in parallel), so job lists built by JavaScript are read too. Sites read this way get their own baseline (`<name>|browser` in `state/seen.json`), so their existing jobs are not reported as new.

## Career-page systems supported
Greenhouse, Lever, Ashby, SmartRecruiters, Personio and Workday, all through their public job feeds. Every other site is handled as `generic`: the crawler reads schema.org `JobPosting` data where the page has it, otherwise job-like links. Undated jobs are dated by the first day the crawler saw them (`dateSource: first_seen`). The first crawl of a site only records its current jobs and reports none of them.

## Files
- `companies.csv`: the employer list (name, domain, type, ats, ats_id, careers_url, …). Edit it by hand to add companies. Set `status` to `inactive` to skip one.
- `state/seen.json`: every job id the crawler has seen, plus the date each site was first crawled.
- `data/ba_employers.csv`: every employer seen in Arbeitsagentur results.

## Cost
€0. Uses public APIs and GitHub Actions free minutes (about 10–20 minutes a day).

## Run locally
```
pip install requests pytest
python -m pytest -q tests
python crawl.py --only "Proxima"      # one company
python detect_ats.py --limit 20
```
