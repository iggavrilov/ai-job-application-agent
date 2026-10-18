# Validation report — 2026-10-16

## Results

- System Python: 3.12.14. Project `.venv`: Python available, pip 25.0.1 only.
- System packages present: HTTPX, Pydantic, PyYAML, python-dotenv, PyMuPDF, pandas.
- Missing in both usable project runtime and system runtime: Streamlit, SQLAlchemy, OpenAI, pytest, filelock. The project venv also lacks the otherwise available system libraries.
- `python -m unittest discover -s tests -p test_available_environment.py -v`: **14 test methods passed** (includes parameterized subtests).
- `python -m compileall -q agent app.py tests`: passed, no syntax errors.
- System imports passed: agent.models, agent.settings, agent.collectors, agent.matching.
- Imports blocked: agent.db (SQLAlchemy), agent.discovery and agent.cli (filelock), app (Streamlit).
- `.venv/bin/python -c 'import agent.models'`: blocked by missing Pydantic.
- `python -m pytest -q`: cannot execute, pytest missing. The original pytest suite has NOT passed or been fully collected.

Offline checks exercise all three collectors via HTTPX MockTransport (no network), original-description preservation, absent salary, keyword filtering, profile validation, conservative eligibility cases, work-authorization uncertainty, scoring, duplicate key generation, HTTP 429/503 failures for each platform, robots restrictions, AI schema rejection, no-key rules and missing-SDK fallback. Mock vacancies remain only in memory.

Initial offline run: 10 methods passed, 4 failed. All four failures traced to the absent `qa automation engineer` include keyword. Added that keyword to the existing YAML configuration. Final run: 14 passed, 0 failed. No application functionality was replaced.

## Installation and network diagnosis

The earlier pip installation could not connect to the inherited proxy because the execution sandbox returned `[Errno 1] Operation not permitted`. The subsequent “No matching distribution” was a consequence of failing to reach the package index, not evidence of incompatible package versions. The cache warning about `/home/agent/.cache/pip` is unrelated to the fatal network error. Two earlier additional-permission installation attempts were interrupted; neither populated the virtual environment.

On this validation run, the cloud environment reports HTTP network policy unrestricted/enforced. However, a single HTTPX GET to `https://boards-api.greenhouse.io/robots.txt` failed immediately with `ConnectError: [Errno 1] Operation not permitted` in the command sandbox. No installation retry, proxy bypass, permission escalation or further live requests were attempted. These are separate policy layers: the permissive cloud HTTP policy does not establish sandbox socket access.

## Unverified and remaining issues

Dashboard runtime, SQLite persistence, end-to-end discovery, search locking/history and the original full pytest suite remain unverified because dependencies are absent. Real OpenAI calls are unverified. Windows execution has not been tested. **Zero real vacancies were retrieved or stored in this session.** Registry identifiers are starter entries awaiting live verification, not verified boards. No CV PDF is available for independent source verification. Geographic and skill interpretation are heuristic; passing representative tests does not establish correct handling of every natural-language restriction. Employer board coverage is limited. No applications or LinkedIn automation were added.

## Exact Windows validation commands

Open PowerShell in the copied project directory (replace the path below):

```powershell
Set-Location C:\path\job-agent
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m compileall -q agent app.py tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m agent.cli discover --no-ai
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Open http://127.0.0.1:8501 after Streamlit starts. Stop on installation/test failures and inspect the error before running later commands. Keep the delivered private `candidate_profile.json` in the project directory; Git deliberately excludes it. OpenAI is optional: set OPENAI_API_KEY in local `.env` and omit `--no-ai` only when ready to enable it. No API key is needed for rule-based matching or public board discovery.

Next action: install dependencies on a machine with permitted package-index access, run the full pytest suite, then verify the three configured endpoints and local dashboard. Do not mark MVP-01 accepted until those checks pass.

## Follow-up development and validation — 2026-10-16

This section supersedes earlier readiness/count statements above. Existing functionality was updated in place; no installations or additional network probes were attempted in this development pass.

### Completed code changes

- Registry validation isolates malformed/duplicate entries and rejects path-like identifiers/unsupported platforms. Verified metadata requires a timezone-aware check time and HTTPS evidence URL. All three existing board entries are explicitly pending; **verified board count remains zero**. No invented employers/identifiers were added.
- Collector normalization skips malformed postings with warnings, supports Ashby public payloads without a separate `id` by retaining the original posting URL as source identity, honors explicit onsite/hybrid/remote metadata and decodes descriptions for reading while retaining source descriptions in storage.
- Conservative matching handles country-only restrictions, contradictory worldwide/location wording, Moldova exclusions and changed candidate residence. Unknown language requirements cause review. Word boundaries avoid treating JavaScript as Java or NoSQL as SQL; unsupported C++ is detected. Salary remains unrestricted and absent salaries are retained. AI cannot remove deterministic uncertainty, and unknown candidate skills cannot enter matching-skills evidence.
- Database integration now rolls back a failed board transaction before continuing, preserving already committed boards. Counts increment only after commit. An additive `job_observations` table retains cross-source identities, canonical URLs and per-source payloads. Exact identities precede fuzzy duplicate keys. Match history includes the evaluated job snapshot and configuration. Relative SQLite paths resolve against the project root; foreign keys and a 30-second database busy timeout are enabled.
- CLI adds `verify-boards`, `doctor` and dependency-light help. `python -m agent.diagnostics` uses the standard library only. Explicit verification records endpoint evidence locally; it does not assert employer ownership or eligibility. Discovery exit code 3 indicates partial results.
- CLI/dashboard share rotating logs. Exception bodies that may contain SQL/profile values are excluded. Dashboard adds region filters/geographic ranking, verification warnings, extracted requirements and last-checked dates. OpenAI is opt-in in the dashboard; configuration/database initialization errors receive actionable local messages.
- Windows setup, Task Scheduler, source verification, private Git delivery and troubleshooting documents are supplied. Ignore patterns cover environment variants, private profile backups and SQLite variants/sidecars. Tracked-source test assertions and instructions no longer embed the candidate's personal name/CV filename.

### Executed checks

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -p test_available_environment.py -v` | 14 passed |
| `python -m unittest discover -s tests -p '*offline.py' -v` | 18 passed |
| Total available offline test methods | **32 passed, 0 failed** (plus subtests) |
| `python -m compileall -q agent app.py tests` | Passed after final source edits |
| `python -m agent.cli --help` | Passed without database/filelock imports |
| `python -m agent.diagnostics` | Correctly reports system package availability, exit 1 for incomplete readiness |
| `.venv/bin/python -m agent.diagnostics` | Runs with only pip installed; correctly reports all required packages absent, exit 1 |
| Temporary Git ignore audit | 13 private paths ignored; 4 source/template paths trackable; temporary repository deleted |
| `python -m pytest -q` | Cannot start: pytest missing |

A newly added regression initially detected incorrect eligibility for “selected countries only.” That restriction handling was fixed; all final offline checks pass. Expected warnings in malformed-posting tests concern in-memory mock data only. Mock board verification was tested with a failed first source followed by successful mocked sources, without network access or live database writes.

`tests/test_persistence_reliability.py` adds three full-environment tests for cross-source identities after posting changes, database rollback/continuation and persisted configuration failures. These tests are **written but not executed**, since pytest/SQLAlchemy/filelock remain absent. Earlier full-suite tests are also unexecuted. No claim is made that database or dashboard runtime behavior has passed validation.

### Remaining limits and handoff

- Missing system packages: Streamlit, SQLAlchemy, OpenAI, pytest, filelock. Project venv has none of the application packages. HTTPX/Pydantic/PyYAML/dotenv/PyMuPDF/pandas are available only in system Python.
- Full pytest, SQLite runtime, source migration/locking/history, Windows execution, Streamlit UI and real OpenAI remain unverified. Syntax/static review does not establish those components work.
- No new network access attempted because the previous socket denial remains unresolved. No dependency installation retries, bypasses or escalations. **Zero real vacancies discovered and zero boards independently verified.** Three starter entries require endpoint and employer-ownership checks; target-region coverage remains incomplete.
- No functioning Git repository is attached and no GitHub access is configured. Source is prepared for safe local review/staging, but no commits, remotes or pushes were made. The private profile stays ignored and must be transferred separately.
- No LinkedIn automation or application submission is present. No demo jobs were inserted.

Next on Windows: follow WINDOWS_SETUP.md, install requirements, run diagnostics and full pytest, verify employer ownership/API access, then run `discover --no-ai` and open the local dashboard. Do not mark MVP-01 accepted until live storage, duplicate detection and UI details pass on the actual machine.

### File inventory for this follow-up

Created: `agent/registry.py`, `agent/diagnostics.py`, `agent/logging_config.py`, `tests/test_reliability_offline.py`, `tests/test_persistence_reliability.py`, `WINDOWS_SETUP.md`, `SOURCE_VERIFICATION.md`, `DELIVERY.md`.

Modified: `agent/models.py`, `agent/settings.py`, `agent/collectors.py`, `agent/matching.py`, `agent/db.py`, `agent/discovery.py`, `agent/cli.py`, `app.py`, `config/company_boards.yaml`, `requirements.txt`, `.gitignore`, `tests/test_agent.py`, `tests/test_available_environment.py`, `README.md`, `VALIDATION.md`.

Existing private `candidate_profile.json` was preserved unchanged and remains excluded from Git.

## MVP-02 — source verification attempt

At 2026-10-16T06:01:18.285387+00:00, one HTTPX GET to the existing Grafana Greenhouse API candidate failed with `ConnectError: [Errno 1] Operation not permitted`, before any HTTP response. Live verification stopped immediately; no further API/careers research, installation retry or network workaround was attempted. See VERIFIED_SOURCES.md for the complete source ledger and Windows alternative.

Current source state supersedes all earlier registry descriptions: **0 verified boards, 0 active boards, 3 disabled pending leads**. Actual QA vacancy totals are unknown; **0 real vacancies retrieved**. The active registry is empty. Existing leads moved unchanged to the separate disabled `config/company_boards.unverified.yaml`. No guessed identifiers or official careers URLs were added.

Minimal verification-command changes allow explicitly selecting a pending registry with `--registry ... --include-disabled`, and recording observed HTTP status, response posting count and QA titles/links. No automatic activation occurs. The default discovery registry remains unchanged in location and contains only verified sources (currently none). No architecture refactor was undertaken.

Executed after code/config changes:

- Available-environment unittest subset: 14 passed.
- Reliability unittest subset: 18 passed.
- Total: **32 passed, 0 failed**.
- Syntax compilation and CLI help: passed.
- Full pytest invocation: cannot start, `No module named pytest`.

Registry/mock verification tests were adjusted to explicitly distinguish inactive research entries from active sources. The original database discovery test now uses mock source fixtures rather than production registry contents; it remains unexecuted with other dependency-blocked pytest tests. Full runtime and Windows/live discovery remain unverified. No LinkedIn automation, job submissions, commits or pushes occurred.
