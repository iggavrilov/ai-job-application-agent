# Source check — 2026-10-18

This report supersedes historical source status. Only public API/careers pages were read. No LinkedIn access or application submission occurred.

| Employer | Observed evidence | Result |
| --- | --- | --- |
| Grafana Labs | [Official careers page](https://grafana.com/careers/) returned 200 and links to `https://job-boards.greenhouse.io/grafanalabs`. Robots-aware collector queried Greenhouse at 2026-10-18T19:25:46.753035Z. | API HTTP 200; 114 postings normalized; zero configured QA-title matches. Exact board ownership confirmed; enabled with timestamp/evidence URL. |
| PostHog | [Official careers page](https://posthog.com/careers) was readable, but no Ashby board link was established. `https://api.ashbyhq.com/robots.txt` returned HTTP 401. | Collector stopped at robots policy; no job API request, status or vacancy count. Keep `posthog` disabled/pending. |
| Wealthsimple | [Official careers page](https://www.wealthsimple.com/en-ca/careers) returned 200 and links to `https://jobs.ashbyhq.com/wealthsimple`. Legacy Lever check at 2026-10-18T19:25:48.862544Z returned HTTP 200 with an empty list. | Correct disabled queue from Lever to Ashby. Ashby API remains unverified because its origin's robots policy is unavailable. Legacy empty response is not proof of zero employer vacancies. |

Live evidence is saved in ignored `data/board_verification.json`. It records the originally probed registry (including legacy Lever), not the corrected queue. No source policy was bypassed. The active registry has one verified board; the queue has two disabled entries. Do not interpret remote branding as Moldova authorization.

Two rule-based discovery runs against Grafana each returned Success, fetched 114, new relevant 0, errors []. SQLite persisted both run records; zero relevant jobs were stored. Real QA detail/review behavior cannot be demonstrated from these sources at this snapshot. Synthetic integration tests separately exercise persistence, rollback, repeated-job deduplication and dashboard review; test records are never inserted into the live database.

Coverage remains narrow. Moldova, Romania, EU/Europe, worldwide remote review, QA title preferences and no salary minimum are preserved. Additional employer boards need observed official links and successful policy-compliant API checks; no identifiers or openings were guessed.
