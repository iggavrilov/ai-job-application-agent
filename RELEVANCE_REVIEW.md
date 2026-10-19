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

Final local discovery run 10 on 2026-10-18 completed successfully using the real private profile and rule-based matching (`--no-ai`). All 30 boards committed; no source failed. Repeating discovery created zero new records.

| Stage | Count |
|---|---:|
| Published and normalized postings | 2,755 |
| Malformed | 0 |
| Title rejected: no QA keyword | 2,283 |
| Title rejected: excluded title | 428 |
| Relevant title observations | 44 |
| Current unique stored records | 44 |
| Main records requiring geographic review | 23 |
| Excluded current records | 21 |
| Confirmed Moldova-accessible records | 0 |

Among the 23 main records, professional evidence is Strong Match for 1, Good Match for 1 and Needs Review for 21. These are evidence labels, not hiring or work-authorization guarantees. All 23 have geographic Needs Review. Exclusions overlap: 20 geography exclusions and 6 professional exclusions, with 5 in both categories, produce 21 excluded records. Observation-level and unique main counts now both equal 23.

The final quality review fixed a confirmed identity defect: the old company/title/location fingerprint merged distinct IDs and URLs. Persistence now matches exact source identity or canonical application URL only. On database open, a transactional, idempotent repair splits historical groups by application URL, moves observations and snapshot-backed matches to the right job, and appends independent assessments if a source lacked attributable history. Historical match payloads are preserved. New split rows start as New; the existing row keeps its review status. Exact-URL aliases remain deduplicated.

Both Bitdefender QA Engineer/Bucharest IDs now have separate database rows, application URLs and assessments:

- [4971534101](https://job-boards.eu.greenhouse.io/scbitdefendersrl/jobs/4971534101) - Needs Review.
- [4926040101](https://job-boards.eu.greenhouse.io/scbitdefendersrl/jobs/4926040101) - Needs Review.

All 22 original main application URLs remain accessible in SQLite and Streamlit; the recovered separate vacancy increases the main list to 23. The database also retains one historical director posting outside the current title filter: the complete dashboard has 45 rows (23 main, 22 audit), while this run has 44 current relevant records. Every audit row retains an exclusion reason.

## Real review leads

Remote status is taken from the source, not inferred from a title. All entries below require employer confirmation of Moldova eligibility; quantity does not establish suitability.

| Employer / title | Advertised location | Remote status | Direct application URL | Selection / concern |
|---|---|---|---|---|
| PadSplit - QA Engineer (Anywhere in Europe) | Ukraine | Remote | https://jobs.lever.co/padsplit/349741f7-56f2-4556-9372-12b24f95b28a | Europe title conflicts with Ukraine location; confirm Moldova hiring |
| Xebia CEE - Senior QA Automation Engineer | Bulgaria; Moldavia; Poland; Romania | Unknown | https://job-boards.greenhouse.io/xebiacee/jobs/5836999004 | Moldavia label conflicts with EU residence/work-permission requirement; confirm country and work mode |
| Bitdefender - QA Engineer | Bucharest | Unknown | https://job-boards.eu.greenhouse.io/scbitdefendersrl/jobs/4926040101 | Romania priority; remote mode and Moldova access unconfirmed; separate job IDs preserved |
| Bitdefender - QA Automation Engineer | Bucharest | Unknown | https://job-boards.eu.greenhouse.io/scbitdefendersrl/jobs/4995086101 | Relevant QA title; professional requirements and hiring access need review |
| Ubiquiti - Manual QA for Networking products | Bucharest, Romania | Unknown | https://job-boards.greenhouse.io/ubiquiti/jobs/4074888009 | Manual QA and Romania; networking requirements, work mode and access need review |

Excluded examples: Xsolla Baku/Kuala Lumpur onsite and Ubiquiti Draper, Utah are outside the target geography. Xebia Senior QA Manual has an explicit EU residence/work-permit requirement without Moldova scope. Exadel AxiomSL requires a core specialization not evidenced in the supplied profile; it is excluded for professional evidence independently of geographic uncertainty. Evidence gaps do not claim inability.

## Validation and limits

Windows Python 3.12.14: **164 passed, 0 failed, 0 skipped, 0 pytest warnings; 48 subtests passed**. The PR entered this review with 152 passed; the earlier main baseline was 114 passed. Direct unittest runs passed 14 environment checks and 18 offline reliability checks; compileall passed. Installed dependency metadata validation found zero missing/incompatible base requirements. Native pip check was unavailable (pip is absent in the 3.12 environment and the system pip is incompatible with Python 3.12). Tests use synthetic fixtures, retain unrelated regression coverage and cover independent axes, explicit/uncertain restrictions, city aliases, Moldavia conflict, EU/Cyprus priority, professional requirements, verified source parsing, transactional diagnostics, duplicate observations, and Streamlit main/audit views.

A final AppTest against the real local SQLite database confirmed all 22 original main URLs remain present, 23 main rows, no Ineligible/Eligible main rows, 22 audit rows with reasons and 45 total rows, without application exceptions. Streamlit emits its existing bare-context warning and use_container_width deprecation outside pytest. Source availability and vacancies change; these are point-in-time API observations. Geographic parsing is heuristic, city coverage is finite, unknown work mode stays unknown, work authorization is not inferred, and no employer confirmed Moldova hiring. AI-enabled matching was not exercised in the live run. No LinkedIn access or application submission occurred.

Run on Windows using WINDOWS_SETUP.md, then `.\.venv\Scripts\python.exe -m agent.cli discover --no-ai` and `.\.venv\Scripts\python.exe -m pytest -q`. Keep profile, CV, credentials and databases local and ignored.


## Final quality review corrections

Residence-duration requirements and nationality wording were incorrectly able to affect professional experience/mandatory scores. Rule-based professional analysis now removes geography-only clauses before extracting skill/experience evidence. EU work permission, citizenship, and residence requirements stay on the eligibility axis. Moldova listed with EU residence/work permission is explicitly described as contradictory and remains Needs Review. Conditional location approval, pending eligibility confirmation, and required relocation cannot become Eligible merely because Moldova/remote appears in the posting. A mocked high-confidence AI assessment also cannot promote uncertain geographic eligibility.

Regression tests now exercise distinct same-title/same-city IDs and URLs with different eligibility, exact-URL alias deduplication, snapshot and pre-snapshot history repair, repeat-open idempotence, rollback, and recovered separate rows in Streamlit. The old tests that explicitly expected fuzzy merging were corrected to assert the new identity policy; their exact-identity/URL dedup and changed-content coverage remain.

No private CV, candidate JSON, credentials or databases are included. Local backup and live diagnostic files remain ignored. New PR commits preserve the user-authorized author metadata. PR #3 has since merged to main.

## Daily-use check — 2026-10-19

After PR #3 merged to main, discovery fetched 2,761 postings from 30/30 boards, normalized all 2,761, rejected 2,717 by title and retained 44 distinct current QA records (0 new on repeat discovery). Current unique-record eligibility is 0 Eligible, 25 Needs Review and 19 Ineligible. Professional evidence is 1 Strong Match, 1 Good Match, 36 Needs Review and 6 Not Recommended. All current vacancy titles, locations, remote labels, match scores, eligibility concerns and original URLs are in a local ignored report at `private/daily-vacancy-review-2026-10-19.md`; it is generated from local SQLite and is not included in Git.

The dashboard now lets the user set New, Reviewed, Shortlisted, Rejected or Applied. Status is saved on the SQLite job row; discovery updates posting and match history without changing that status. Automated tests exercised all four manual states over two repeated discoveries. The full Windows Python 3.12.14 suite now reports 168 passed and 48 subtests passed, with zero failures, skips or pytest warnings.
