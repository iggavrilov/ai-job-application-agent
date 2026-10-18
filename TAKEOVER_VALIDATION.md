# Takeover validation — 2026-10-18

## Repository provenance

Authenticated live GitHub API reads confirmed private repository `iggavrilov/ai-job-application-agent`, default branch `main`, SHA `a2923007d9349bd5f0f96ed976cb1374739055df`. The root contents were read through the API before cloning. A fresh clone matched this SHA, then switched to `fix/profile-and-discovery-validation`. The unrelated original workspace was not modified. Main is not modified or merged.

Work ran on Windows, not the historical managed Linux cloud workspace. Dependencies were installed in an isolated local venv. No credentials were added to source.

## Architecture and root causes

- `settings` reads configuration/private Candidate data; Pydantic validates models. ATS collectors use public APIs with robots checks and normalize descriptions. Rules/optional OpenAI produce matches. Discovery locks runs, commits each board separately, rolls back failed boards, deduplicates job observations, and appends match history in SQLAlchemy/SQLite. CLI and Streamlit expose discovery/review; neither submits applications.
- All observed baseline failures depended on the missing Git-ignored `candidate_profile.json`. Matching/unit tests loaded private data; discovery tests failed during setup before reaching their mocked boards, producing Failed instead of Partial. This was not evidence that rollback logic was defective. Existing rollback/continuation and history assertions now pass with synthetic candidate inputs.
- Profile loading rejected Windows UTF-8 BOM files. Direct Pydantic errors included private input values. Added regression tests first: 3 failed, 1 passed; fixed BOM handling and safe missing/invalid/read-error guidance without exposing inputs.
- Three baseline serializer warnings came from test `model_copy` updates inserting strings into `HttpUrl` fields. Fixtures now use typed URLs; warnings were not suppressed.
- `remote_preferred` existed in YAML but was unused by dashboard sorting. Ranking now prioritizes remote roles; geographic mode retains Moldova → Romania → EU/Europe → international → other ordering. No minimum salary was introduced.
- Live employer research found Wealthsimple's official site now links to Ashby, not the legacy Lever board. Corrected its disabled queue entry. Enabled Grafana only after exact official link and successful public API check; see source report.

## Executed validation

Windows Python 3.11.0 and bundled Python 3.12.14, pytest 9.1.1, Pydantic 2.14.1, SQLAlchemy 2.1.5, Streamlit 1.66.0, HTTPX 0.28.1.

| Check | Result |
| --- | --- |
| Fresh main full pytest baseline | 36 passed, 43 failed, 0 skipped, 3 warnings; 34 passing subtests |
| Full pytest after fixes and added coverage | 73 passed, 0 failed, 0 skipped, 0 warnings; 48 passing subtests |
| Full final pytest on Python 3.12.14 | 73 passed, 0 failed, 0 skipped, 0 warnings; 48 passing subtests |
| Dependency consistency: `pip check` | No broken requirements |
| `unittest` available-environment subset | 14 passed |
| `unittest` reliability subset | 18 passed |
| `compileall` agent, app, tests | Passed |
| Private real profile loaded via production loader | Candidate schema passed; content never committed |
| Two real `discover --no-ai` runs | Both Success, fetched 114, new relevant 0, errors [] |
| SQLite inspection after live runs | Two persisted Success records; zero stored relevant jobs |

Tests retain existing assertions and cover negative candidate years, scoring, geography, AI fallback/uncertainty, collector failures, malformed postings, deduplication, changed cross-source identity, DB rollback/continuation, lock overlap and safe persisted errors. New tests cover private profile file loading and Streamlit empty/populated states, marking Reviewed and ranking preferences. Synthetic jobs remain in temporary test databases only.

Streamlit AppTest initially stalled in Python's Windows asyncio `socketpair` creation under the sandbox. A traceback identified the blocked loopback operation; rerunning with permitted local socket access passed. No test was skipped or bypassed. Tests isolate discovery/database/log roots, credentials and profile inputs. The unittest subsets also work without private files. Registry unit tests now use temporary registries so real verified-source updates do not dictate their fixtures.

## Private candidate handling

The user-selected PDF was read locally and all three pages visually inspected. A real `candidate_profile.json` was created locally and validated with `agent.settings.profile()`. Extracted fields include name/residence, conservative QA experience, role/employer/date strings, technologies and QA skills, project descriptions and listed certifications. The existing schema represents work history in string lists; no unnecessary schema migration was made. Facts are CV-reported, not independently credential-verified. Unspecified languages, contact details, salary, citizenship and authorization remain unknown. No qualification, metric or project-specific technology assignment was invented.

The PDF remains outside the checkout. The profile, review images, reports, logs, local SQLite data and virtual environments are Git-ignored. Only explicitly synthetic candidate inputs are tracked. Restore the private profile separately after a new clone; factual details and the CV filename are intentionally absent from this report.

## Remaining limits and Windows operation

Only one active verified board is available; it currently supplies no relevant QA jobs. Live QA ingestion/detail display is therefore unproven with actual matching vacancies, while synthetic integration coverage passes. Ashby robots HTTP 401 prevents policy-compliant API verification; no bypass was attempted. No real OpenAI request was made. Eligibility matching remains heuristic and cannot establish work authorization. Existing same-title/location fuzzy deduplication may merge distinct vacancies, and removed-posting lifecycle handling is unchanged.

Follow [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for environment creation, private CV/profile transcription and safe validation, source verification, CLI discovery, local dashboard and daily scheduling. Tests need installed requirements only, not private files. Preserve `.env`, profile and data privately. Keep Streamlit bound to `127.0.0.1`. The full final suite passed on both Windows Python 3.11.0 and 3.12.14. Newer Python versions remain unverified. The bundled 3.12 venv initially could not bootstrap ensurepip; upgrading pip in the isolated 3.11 venv allowed installing the same requirements into the separate 3.12 venv using pip --python. No global runtime was modified.

No LinkedIn account was accessed, no applications were submitted, no candidate information or local database was published, and no automatic merge occurred.
