# Verified QA discovery — 2026-10-18

## Provenance and result

Started from live `main` SHA `ce6a4b55ea0d6fd321968ce50d11e4b792309ef9`, the confirmed merge commit of PR #1. Work is on `feat/verified-qa-discovery`; main is not modified. The private candidate profile was available, schema-validated and used for rule-based matching locally. No CV, profile, credentials, local database, match skill lists or personal qualifications are included in Git. No LinkedIn access, applications, access-policy bypass or automatic merge occurred.

**27 verified active boards: 22 Greenhouse and 5 Lever; 0 Ashby.** Each has an exact board link or embedded API URL observed on its official employer careers page/linked subpage, plus a successful robots-aware API response. Starburst's accessible talent-community/event board is deliberately excluded from active counts. Public search results were research leads only; vacancy counts below came from actual API ingestion.

## Why the previous 114 postings produced zero matches

The only active board was Grafana. Re-fetching its 114 postings and applying the previous main rules rejected all 114 by title before candidate scoring, eligibility or SQLite upsert. The new diagnostic classification identifies 96 without a QA title keyword and 18 excluded titles. Expanded QA-title variants still yield zero QA roles on that particular board. Source coverage, not salary or location rejection, caused that zero result.

The old include list also missed legitimate forms such as Test Automation Engineer, Software Engineer in Test, SDET and QA Automation Specialist. The expanded list retains software-testing evidence requirements for generic Quality/Test Engineer titles and excludes programming/developer, architect and QA director/manager roles. Uncertain mandatory skills and geographic eligibility remain reviewable.

## Final live pipeline and persistence

Counts refer to a single complete 27-board run (run 6), not a sum of research and repeated fetches. All 27 boards committed; no collection or database errors occurred. Repeating discovery verified stable identity/deduplication and appended match history.

| Stage or outcome | Count |
| --- | ---: |
| Actual API postings fetched | 2,453 |
| Successfully normalized | 2,453 |
| Malformed postings skipped | 0 |
| Title-filter rejections | 2,443 |
| Rejected: no QA title keyword | 2,057 |
| Rejected: excluded title | 386 |
| Rejected: no software-testing evidence / advanced-programming SDET | 0 / 0 |
| Relevant QA/test postings retained and evaluated | 10 |
| Eligibility Needs Review | 9 |
| Eligibility Ineligible (advertised US onsite role) | 1 |
| Confirmed Eligible | 0 |
| Dropped solely for uncertain eligibility, score or missing salary | 0 |
| New jobs inserted on repeat run | 0 |
| Existing/merged postings updated on repeat run | 10 |
| Failed boards / rolled-back postings | 0 / 0 |

`new_relevant=0` on the repeat run means the ten vacancies were already persisted; it does not mean zero QA matches. The first full 27-board pass added three jobs to the seven current matches already found during coverage expansion. SQLite now contains ten distinct current relevant vacancies and their match history. One QA director found during an intermediate run remains in historical storage but is excluded from the current dashboard by the requested engineer-level search rules; no historical records were deleted.

Added the `search_diagnostics` table without altering existing table schemas. Each run records per-board observed/unknown counts, exact rejection categories, eligibility, recommendations and committed/rolled-back stages. Failed collection has null source counts, not a fabricated zero. Diagnostics appear in CLI JSON and the dashboard Discovery diagnostics expander. Totals distinguish normalized observations from committed persistence.

## Actual vacancies

All links below came from normalized API records. These are leads for review, not assertions of candidate fit or authorization. The closest geographic leads are PadSplit and the Exadel AxiomSL role. Exadel requires specialist regulatory-reporting experience that must be checked; no such experience was invented. PadSplit's title says Anywhere in Europe while its ATS location says Ukraine; Moldova acceptance and employment terms require confirmation.

| Employer | Title and direct posting URL | ATS location | Mode | Eligibility |
| --- | --- | --- | --- | --- |
| PadSplit | [QA Engineer (Anywhere in Europe)](https://jobs.lever.co/padsplit/349741f7-56f2-4556-9372-12b24f95b28a) | Ukraine | Remote | Needs Review |
| Exadel | [QA Engineer (AxiomSL)](https://job-boards.greenhouse.io/exadelinc/jobs/6183595004) | Bulgaria, Lithuania, Poland, Romania | Remote | Needs Review |
| Exadel | [Lead Automation QA Engineer (Calypso)](https://job-boards.greenhouse.io/exadelinc/jobs/6206383004) | Washington, DC, USA | Onsite | Ineligible |
| Exadel | [Middle QA Engineer (Capital Markets)](https://job-boards.greenhouse.io/exadelinc/jobs/6218340004) | Brazil | Unknown | Needs Review |
| Canonical | [Software Quality Assurance Engineer - Linux, PC, IoT](https://job-boards.greenhouse.io/canonical/jobs/8248986) | Office Based - Beijing, China | Onsite | Needs Review |
| Canonical | [Software Quality Assurance Engineer - Linux, PC, IoT](https://job-boards.greenhouse.io/canonical/jobs/7975990) | Office Based - Taipei, Taiwan | Onsite | Needs Review |
| Canonical | [Ubuntu Linux Kernel Test Engineer](https://job-boards.greenhouse.io/canonical/jobs/7965671) | Home Based - APAC; Office Based - Beijing, China; Office Based - Taipei, Taiwan | Unknown | Needs Review |
| Xsolla | [Middle QA Engineer](https://jobs.lever.co/xsolla/4b7879d6-0250-4717-9514-7296e8645ee1) | Baku | Onsite | Needs Review |
| Xsolla | [Senior QA Automation Engineer KL](https://jobs.lever.co/xsolla/1dd5a9e7-9298-432d-906b-04d24ce57223) | Kuala Lumpur | Onsite | Needs Review |
| Xsolla | [Senior QA Engineer (Web3 Applications & Infrastructure)](https://jobs.lever.co/xsolla/f5b67c57-4878-4410-a709-4ef29b45d7f9) | Kuala Lumpur | Onsite | Needs Review |


## Source ledger

API verification times are UTC. Posting/QA counts are from the complete final ingestion snapshot and can change. Official URLs are evidence of observed links, not promises of Moldova hiring.

| Employer | Platform / exact board | Official careers evidence | Verified at (UTC) | Fetched | QA/test retained |
| --- | --- | --- | --- | ---: | ---: |
| Aircall | greenhouse / `aircallioinc` | [Official page](https://aircall.io/careers/) | 2026-10-18T19:44:54.217021+00:00 | 78 | 0 |
| Bungie | greenhouse / `bungie` | [Official page](https://careers.bungie.com/) | 2026-10-18T19:49:56.725749+00:00 | 4 | 0 |
| Canonical | greenhouse / `canonical` | [Official page](https://canonical.com/careers) | 2026-10-18T19:42:52.788949+00:00 | 309 | 3 |
| Collabora | lever / `collabora` | [Official page](https://www.collabora.com/careers.html) | 2026-10-18T19:43:04.311137+00:00 | 2 | 0 |
| Customer.io | greenhouse / `customerio` | [Official page](https://customer.io/careers) | 2026-10-18T19:46:36.710680+00:00 | 27 | 0 |
| Dataiku | greenhouse / `dataiku` | [Official page](https://www.dataiku.com/careers/) | 2026-10-18T19:44:51.506371+00:00 | 13 | 0 |
| Exadel | greenhouse / `exadelinc` | [Official page](https://exadel.com/careers/) | 2026-10-18T19:52:17.648603+00:00 | 38 | 3 |
| GitLab | greenhouse / `gitlab` | [Official page](https://about.gitlab.com/jobs/) | 2026-10-18T19:42:56.102559+00:00 | 232 | 0 |
| GoCardless | greenhouse / `gocardless` | [Official page](https://gocardless.com/about/careers/) | 2026-10-18T19:43:18.176894+00:00 | 32 | 0 |
| Grafana Labs | greenhouse / `grafanalabs` | [Official page](https://grafana.com/careers/) | 2026-10-18T19:42:53.293772+00:00 | 114 | 0 |
| Harness | greenhouse / `harnessinc` | [Official page](https://www.harness.io/company/careers) | 2026-10-18T19:44:51.455098+00:00 | 67 | 0 |
| Intercom | greenhouse / `intercom` | [Official page](https://www.intercom.com/careers) | 2026-10-18T19:46:37.474147+00:00 | 106 | 0 |
| LaunchDarkly | greenhouse / `launchdarkly` | [Official page](https://launchdarkly.com/careers/) | 2026-10-18T19:43:21.050002+00:00 | 52 | 0 |
| Lokalise | greenhouse / `lokalise` | [Official page](https://lokalise.com/careers/) | 2026-10-18T19:45:31.371665+00:00 | 2 | 0 |
| Mattermost | greenhouse / `mattermost` | [Official page](https://mattermost.com/careers/) | 2026-10-18T19:45:29.589247+00:00 | 11 | 0 |
| Mixpanel | greenhouse / `mixpanel` | [Official page](https://mixpanel.com/careers/) | 2026-10-18T19:46:38.892329+00:00 | 47 | 0 |
| Monzo | greenhouse / `monzo` | [Official page](https://monzo.com/careers) | 2026-10-18T19:46:41.397323+00:00 | 71 | 0 |
| Neo4j | greenhouse / `neo4j` | [Official page](https://neo4j.com/careers/) | 2026-10-18T19:45:55.547065+00:00 | 65 | 0 |
| Netlify | greenhouse / `netlify` | [Official page](https://www.netlify.com/careers/) | 2026-10-18T19:43:13.622989+00:00 | 5 | 0 |
| PadSplit | lever / `padsplit` | [Official page](https://www.padsplit.com/careers) | 2026-10-18T19:49:47.799220+00:00 | 9 | 1 |
| Palantir | lever / `palantir` | [Official page](https://www.palantir.com/careers/) | 2026-10-18T19:46:27.014778+00:00 | 310 | 0 |
| Sauce Labs | greenhouse / `saucelabs` | [Official page](https://saucelabs.com/careers) | 2026-10-18T19:44:41.917692+00:00 | 9 | 0 |
| Sourcegraph | greenhouse / `sourcegraph91` | [Official page](https://sourcegraph.com/careers) | 2026-10-18T19:45:59.237049+00:00 | 9 | 0 |
| Speechify | greenhouse / `speechify` | [Official page](https://speechify.com/careers/) | 2026-10-18T19:44:59.871700+00:00 | 492 | 0 |
| Unlimit | lever / `unlimit` | [Official page](https://www.unlimit.com/careers/) | 2026-10-18T19:46:28.592569+00:00 | 64 | 0 |
| Vercel | greenhouse / `vercel` | [Official page](https://vercel.com/careers) | 2026-10-18T19:43:14.260549+00:00 | 93 | 0 |
| Xsolla | lever / `xsolla` | [Official page](https://xsolla.com/careers) | 2026-10-18T19:49:53.522555+00:00 | 192 | 3 |

Observed linked subpage URLs and source counts are also retained privately in `data/active-source-evidence.json`. The registry contains reproducible official source URLs and timestamps. A successful API alone never establishes ownership; `verify-employer` requires an official HTTPS source, extracts exact observed identifiers, verifies API access, and does not write the registry automatically. Redirect targets receive their own robots check before fetching.

## Tests and dashboard validation

- Merged-main baseline on Windows Python 3.12.14: **73 passed, 0 failed, 0 skipped, 0 pytest warnings; 48 passing subtests**.
- Final full suite: **114 passed, 0 failed, 0 skipped, 0 pytest warnings; 48 passing subtests**.
- Offline unittest subsets: **14 passed + 18 passed**.
- Syntax compilation and dependency consistency checks passed.
- New regression coverage: exact source identifiers/spoofed hosts, official ownership links and subpages, blocked robots and redirects, QA title variants, non-software quality roles, regional scope, home/office work modes, diagnostic accounting/persistence, failed-source unknown counts and duplicate rediscovery.
- A live-reviewed defect initially treated Anywhere in Europe as worldwide eligibility. A focused regression failed before the fix; regional or specific-country wording now stays Needs Review. Mixed US/EU locations also remain reviewable. Explicit Work Type: Remote is recognized without overriding an explicit onsite location.
- Streamlit AppTest against the actual private SQLite database displayed **10 current relevant vacancies**, opened PadSplit and Exadel details, and retained **2 remote rows** under the Romania + EU/Europe filters, with **0 application exceptions**. No live review statuses were changed. Synthetic dashboard regression also verifies that excluded historical jobs remain in SQLite while current metrics omit them.
- Streamlit tests need permitted Windows loopback sockets. No tests were skipped or bypassed. Standalone AppTest logs include Streamlit's bare-context notice and its existing `use_container_width` deprecation notice; these are not pytest warnings or application exceptions.

## Remaining limitations and operation

- No confirmed Moldova-authorized vacancy was established. Nine leads need manual eligibility review; an advertised US onsite role remains visible as Ineligible/Not Recommended rather than being counted as suitable. The non-target Asia/Azerbaijan/Brazil roles have substantial location or specialist-skill limitations. No strong-match claim is made.
- Ashby API-origin robots returns HTTP 401; no Ashby job API was queried and no Ashby source was activated. Some official pages were blocked, unavailable, dynamic or had no supported ATS link. No identifiers were guessed to fill coverage.
- The source verifier proposes evidence from observed links; the operator must supply a real official employer URL and review employer identity, policies and regional relevance. A talent community is not counted as a vacancy source.
- Geographic and mandatory-skill interpretation remains conservative heuristic processing. Salary has no minimum, and missing salary never reduces score. Real OpenAI calls were not made; matching used the existing private profile locally with `--no-ai`.
- Existing fuzzy same-company/title/location deduplication can merge distinct source jobs; exact source identities and observations remain preserved. Removed-posting lifecycle tracking is not implemented. Vacancy availability can change after this snapshot.
- Original descriptions and private profile snapshots stay in the ignored local database. Public reports contain only employer/vacancy metadata and aggregate validation outcomes.

Run from Windows PowerShell after installing requirements and restoring the private profile:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m agent.cli discover --no-ai
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

To research an additional official source without activating it:

```powershell
.\.venv\Scripts\python.exe -m agent.cli verify-employer --company "PadSplit" --careers-url "https://www.padsplit.com/careers"
```

Review source evidence and endpoint status before adding verified entries. See WINDOWS_SETUP.md for private profile validation, installation and scheduling.
