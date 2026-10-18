# AI Job Application Agent — MVP-01

Local Python 3.12+ Streamlit app for discovering and reviewing QA vacancies. No application submission, LinkedIn automation, browser automation, or authenticated employer access.

## Windows setup (PowerShell)

Open a terminal in this project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Open http://127.0.0.1:8501. Keep `candidate_profile.json` locally; it is deliberately ignored by Git. If copying a tracked checkout, supply this private file separately using `agent.models.Candidate` as the schema. The delivered file contains only facts supplied by the user; no PDF attachment was available, so CV verification has not been performed. Unknown contact details, citizenship, work authorization and salary preferences stay unknown; no language proficiency is inferred. Optional local PDF extraction:

```powershell
.\.venv\Scripts\python.exe -m agent.cli extract-cv --pdf "C:\private\candidate-cv.pdf"
```

Extraction prints text locally; review it before manually updating the profile. No automated extraction invents or overwrites qualifications.

## Discovery and tests

```powershell
.\.venv\Scripts\python.exe -m agent.cli discover --no-ai
.\.venv\Scripts\python.exe -m pytest -q
```

Remove `--no-ai` and set `OPENAI_API_KEY` in `.env` to enable structured OpenAI compatibility analysis. `OPENAI_MODEL` defaults to `gpt-4o-mini`. Employer APIs receive only their board identifier. When explicitly enabled, the OpenAI API receives structured profile facts and job descriptions, excluding candidate email/telephone, never the full CV. OpenAI failure or invalid JSON falls back to rules. Untrusted posting instructions are treated as data.

Linux/macOS: use `python3 -m venv .venv` and `.venv/bin/python` in place of the Windows interpreter path. Run commands from the project folder; relative DATABASE_URL overrides resolve against the project directory.

## Configuration

`config/company_boards.yaml` controls employer registry entries (`company`, `platform`, `board`, `enabled`). Add employers without code changes. These APIs have no global keyword search. The small starter registry is infrastructure, not an exhaustive Moldova/EU employer list. Expand it with verified employer boards for useful coverage.

Public API documentation:

- Greenhouse: https://developers.greenhouse.io/job-board.html — `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true`
- Lever: https://github.com/lever/postings-api — `GET https://api.lever.co/v0/postings/{board}?mode=json`
- Ashby: https://developers.ashbyhq.com/docs/public-job-posting-api — `GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true`

`config/search_rules.yaml` controls title keywords, weights and request interval. Salary minimum is null: salary is preserved but never penalized, and its 15 points redistribute in proportion to 35:20:20:10. Non-null salary preferences deliberately raise a configuration error until currency/period-aware comparison is implemented. Unknown language proficiency receives neutral additional-requirement credit, never an assertion of proficiency.

Moldova is the candidate's only verified residence. EU countries (including Cyprus in the same category) and Romania are reviewable opportunities; EU residence/authorization is never assumed. Worldwide remote and explicit Moldova remote can qualify if no detected restrictions exist. Other locations, vague Europe/EMEA remote scopes, onsite relocation and unknown authorization need review. Country detection is conservative heuristic text processing, not a legal eligibility determination. Relevance filtering keeps Senior and Automation QA titles. Rule-based mandatory requirement detection intentionally favors review. All original descriptions are stored verbatim; HTML is rendered as plain text for safety. Missing structured requirements remain `unknown`; full descriptions are always available to matching.

## Scheduling on Windows

In Task Scheduler, create a task for the current user. Set a daily trigger and these action fields (replace the path):

- Program: `C:\path\job-agent\.venv\Scripts\python.exe`
- Arguments: `-m agent.cli discover`
- Start in: `C:\path\job-agent`

Choose “Do not start a new instance” in task settings. An OS-backed file lock also prevents concurrent dashboard/CLI searches. Logs are at `data/agent.log`; search outcomes/errors are persisted. Interrupted runs can remain marked Running in history; the OS releases the lock, allowing the next search. CLI exit codes: 0 success, 1 failed, 2 overlap, 3 partial (review warnings).

## Persistence and privacy

SQLite tables: candidate_profiles, companies, job_sources, jobs, job_observations, job_matches, search_runs, application_records (reserved, never submitted). Jobs retain first discovered and last checked dates. Latest source content is updated on rediscovery. Match history appends with candidate snapshot, job-description snapshot and logic/config version. Source identities and latest per-source payloads are retained in the additive job_observations table. Deduplication uses platform/board/source ID, canonical URL and normalized company/title/location; same-title vacancies at one location may merge. No automatic deletion or application occurs. Search history distinguishes fetched board postings from stored relevant vacancies. “New” means not reviewed.

Local profile, `.env`, PDFs, logs and databases are excluded from Git. Keep the local dashboard bound to 127.0.0.1. No credentials ship with the project. No fabricated live or demo results are used.

## Validation limits

Linux automated tests and live-source results are recorded below after execution. Windows commands and file locking are designed for Windows but require a real Windows verification run. Public source availability changes; inaccessible boards are logged and other boards continue. Robots directives are checked before each board request, HTTP errors (including 429) stop that board without aggressive retry. Respect employer policies when adding registry entries. No removed-posting lifecycle tracking is implemented in MVP-01.

## Latest validation

See [VALIDATION.md](VALIDATION.md) for the 2026-10-16 evidence and remaining blockers. The available-environment offline subset passed 14 test methods and syntax compilation passed. The full pytest suite, database runtime, Streamlit dashboard and live discovery remain unverified because required packages are missing and the command sandbox blocks network connections. No real vacancies were discovered. Offline subset command (requires HTTPX, Pydantic, PyYAML and python-dotenv):

```powershell
python -m unittest discover -s tests -p test_available_environment.py -v
```


## Windows handoff and source readiness

Follow [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for installation, daily Task Scheduler setup and troubleshooting. [SOURCE_VERIFICATION.md](SOURCE_VERIFICATION.md) documents all three pending starter entries; zero boards are independently verified. Run `python -m agent.cli verify-boards` only with permitted network access. It saves endpoint evidence locally without marking employer ownership verified. `python -m agent.diagnostics` runs a standard-library-only readiness check even in an otherwise empty venv.

The dashboard defaults to rule-based matching; its OpenAI checkbox is opt-in. Region filters group Cyprus with EU countries and support geographic-priority ranking. A failed board transaction rolls back before processing the next board. Malformed postings are isolated with warnings. Logs from dashboard and CLI rotate in `data/agent.log`. The full runtime remains unverified in the managed environment.

See [DELIVERY.md](DELIVERY.md) before putting this source into a private Git repository. Keep the private profile separately; no repository was created or pushed in this environment.


## MVP-02 source verification status

[VERIFIED_SOURCES.md](VERIFIED_SOURCES.md) records the blocked API check. Verified boards: 0; active boards: 0. All three existing leads are disabled in `config/company_boards.unverified.yaml`. No real QA vacancies were retrieved. The active registry intentionally stays empty until sources pass API and employer-ownership verification. With permitted network access, explicitly check pending entries using `python -m agent.cli verify-boards --registry config/company_boards.unverified.yaml --include-disabled`; this does not activate them. Earlier starter-registry descriptions above are historical and are superseded by this status.
