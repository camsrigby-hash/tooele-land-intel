# PMN Coverage Map

Tracks which jurisdictions publish to utah.gov/pmn and which require custom scrapers or are explicitly out of scope.

## PMN-enabled (scraped via scrape_pmn_all.py)

| Jurisdiction | PMN Bodies | Added |
|---|---|---|
| Erda | City Council (7509), Planning Commission (7563) | Phase 1 |
| Grantsville | City Council (1840), Planning Commission (1841) | Phase 1 |
| Tooele City | City Council (685), Planning Commission (687) | Phase 9 |
| Lehi | City Council (2512), Planning Commission (2651) | Phase 9 |
| Saratoga Springs | City Council (1727), Planning Commission (1854) | Phase 9 |
| Eagle Mountain | City Council (535), Planning Commission (536) | Phase 9 |
| South Jordan | City Council (1031), Planning Commission (1032) | Phase 9 |
| Herriman | City Council (1155), Planning Commission (1151) | Phase 9 |
| Bluffdale | City Council & Planning Commission joint body (2803) | Phase 9 |
| Draper | City Council (5555), Planning Commission (383) | Phase 9 |
| American Fork | City Council (180), Planning Commission (183) | Phase 9 |
| Vineyard | City Council (530), Planning Commission (531) | Phase 9 |
| Spanish Fork | City Council (5), Planning Commission (6) | Phase 9 |

## Non-PMN / custom scraper required

| Jurisdiction | Reason | Status |
|---|---|---|
| Stansbury Park | Unincorporated Tooele County — no independent PMN body. Falls under Tooele County which uses Tyler Meeting Manager (JS-rendered SPA). | **Skipped** — per PROJECT_STATE.md scope decision. Tyler Meeting Manager requires Playwright; outside MVP scope. |
| Lake Point | Unincorporated Tooele County — same as Stansbury Park. | **Skipped** — same reason. |
| Tooele County (unincorporated) | Tyler Meeting Manager SPA. | **Skipped** — explicitly out of scope per PROJECT_STATE.md SCOPE RESET (April 22, 2026). |

## Not yet investigated

| Jurisdiction | Notes |
|---|---|
| Salt Lake City | Large city; in JURISDICTIONS enum but very high volume and outside Tooele Valley core focus. PMN bodies likely exist but adding may generate noise. Defer to user decision. |
