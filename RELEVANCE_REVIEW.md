# Geography and professional relevance review

PR #2 was merged only after GitHub confirmed its reviewed head `c3720abd840a316eb7c49431a8474245ca404d75` and main `ce6a4b55ea0d6fd321968ce50d11e4b792309ef9` were unchanged. This branch starts from merge commit `74f1c5c537d29bea4e11c2c5c5cb63055245b002`. Commit-author metadata is explicitly authorized; history was not rewritten.

## Behavior

Professional score normalizes technical, experience and additional evidence independently of geography and salary. Professional evidence gaps, mandatory requirements and seniority are explicit. Geographic eligibility, reason and priority are separate. Default ranking prioritizes confirmed Moldova remote access, possible Moldova remote access, Moldova, Romania, then all EU members equally (including Cyprus). Unknown Moldova access remains Needs Review. Definite outside-country/onsite restrictions and core professional specialization gaps are excluded from the main list, with reasons preserved in the audit view and SQLite. No minimum salary is imposed.

Legacy saved matches are reassessed using their local candidate snapshots without rewriting match history. Missing snapshots fail closed. The private CV/profile was used locally; neither is included here. No candidate-specific skill inventory, contact data or per-vacancy private scores are published.

## Official source expansion

The registry now contains 30 enabled employer boards (25 Greenhouse, 5 Lever). The previous 27 retain their official evidence in the registry and historical QA_DISCOVERY_REPORT.md. Three additional exact board identifiers were verified against official employer pages on 2026-10-18; public API responses were HTTP 200:

| Employer | Official evidence | Board | Published at verification |
|---|---|---|---:|
| Bitdefender | https://www.bitdefender.com/en-us/company/careers/jobs | scbitdefendersrl | 59 |
| Ubiquiti | https://careers.ui.com/ | ubiquiti | 173 |
| Xebia CEE | https://xebia.com/careers/ | xebiacee | 70 |

Bitdefender exposes the exact token through a job-alert link; verification parses that public link locally and never visits login or creates alerts. EU Greenhouse hosts are recognized. Robots-blocked sources were not activated; Ashby remains unused. No invented boards or guessed vacancies.

## Actual live results

Final local discovery run 9 on 2026-10-18 completed successfully using the real private profile and rule-based matching (`--no-ai`). All 30 boards committed; no source failed. Repeating discovery created zero new records.

| Stage | Count |
|---|---:|
| Published and normalized postings | 2,755 |
| Malformed | 0 |
| Title rejected: no QA keyword | 2,283 |
| Title rejected: excluded title | 428 |
| Relevant title observations | 44 |
| Current unique stored records | 43 |
| Main records requiring geographic review | 22 |
| Excluded current records | 21 |
| Confirmed Moldova-accessible records | 0 |

Among the 22 main records, professional evidence is Strong Match for 1, Good Match for 1 and Needs Review for 20. These are evidence labels, not hiring or work-authorization guarantees. All 22 have geographic Needs Review. Exclusions overlap: 20 geography exclusions and 6 professional exclusions, with 5 in both categories, produce 21 excluded records. Observation-level main count is 23; use unique counts for the dashboard.

The existing fuzzy deduplicator groups two Bitdefender QA Engineer/Bucharest source IDs into one record. They may be distinct openings: both observations and direct URLs are retained and exposed in job details. This inherited behavior is documented rather than claiming perfect deduplication. The database also retains one historical director posting outside the current title filter: the complete dashboard therefore has 44 rows (22 main, 22 audit), while this run has 43 current relevant records.

## Real review leads

Remote status is taken from the source, not inferred from a title. All entries below require employer confirmation of Moldova eligibility; quantity does not establish suitability.

| Employer / title | Advertised location | Remote status | Direct application URL | Selection / concern |
|---|---|---|---|---|
| PadSplit - QA Engineer (Anywhere in Europe) | Ukraine | Remote | https://jobs.lever.co/padsplit/349741f7-56f2-4556-9372-12b24f95b28a | Europe title conflicts with Ukraine location; confirm Moldova hiring |
| Xebia CEE - Senior QA Automation Engineer | Bulgaria; Moldavia; Poland; Romania | Unknown | https://job-boards.greenhouse.io/xebiacee/jobs/5836999004 | Moldavia label conflicts with EU residence/work-permission requirement; confirm country and work mode |
| Bitdefender - QA Engineer | Bucharest | Unknown | https://job-boards.eu.greenhouse.io/scbitdefendersrl/jobs/4926040101 | Romania priority; remote mode and Moldova access unconfirmed; grouped posting caveat above |
| Bitdefender - QA Automation Engineer | Bucharest | Unknown | https://job-boards.eu.greenhouse.io/scbitdefendersrl/jobs/4995086101 | Relevant QA title; professional requirements and hiring access need review |
| Ubiquiti - Manual QA for Networking products | Bucharest, Romania | Unknown | https://job-boards.greenhouse.io/ubiquiti/jobs/4074888009 | Manual QA and Romania; networking requirements, work mode and access need review |

Excluded examples: Xsolla Baku/Kuala Lumpur onsite and Ubiquiti Draper, Utah are outside the target geography. Xebia Senior QA Manual has an explicit EU residence/work-permit requirement without Moldova scope. Exadel AxiomSL requires a core specialization not evidenced in the supplied profile; it is excluded for professional evidence independently of geographic uncertainty. Evidence gaps do not claim inability.

## Validation and limits

Windows Python 3.12.14: **152 passed, 0 failed, 0 skipped, 0 pytest warnings; 48 subtests passed**. The inherited baseline was 114 passed. Additional direct unittest checks passed (14 environment tests and 18 offline reliability tests); compileall succeeded. Tests use synthetic fixtures, preserve existing expectations and cover independent axes, explicit/uncertain restrictions, city aliases, Moldavia conflict, EU/Cyprus priority, professional requirements, verified source parsing, transactional diagnostics, duplicate observations, and Streamlit main/audit views.

A final AppTest against the real local SQLite database confirmed 22 main rows, PadSplit first, no Ineligible main rows, 22 audit rows and 44 total rows, without application exceptions. Streamlit emits its existing bare-context warning and use_container_width deprecation outside pytest. Source availability and vacancies change; these are point-in-time API observations. Geographic parsing is heuristic, city coverage is finite, unknown work mode stays unknown, work authorization is not inferred, and no employer confirmed Moldova hiring. AI-enabled matching was not exercised in the live run. No LinkedIn access or application submission occurred.

Run on Windows using WINDOWS_SETUP.md, then `.\.venv\Scripts\python.exe -m agent.cli discover --no-ai` and `.\.venv\Scripts\python.exe -m pytest -q`. Keep profile, CV, credentials and databases local and ignored.
