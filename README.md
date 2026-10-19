# AI Job Application Agent вЂ” MVP-01

Local Python 3.12+ Streamlit app for discovering and reviewing QA vacancies. No application submission, LinkedIn automation, browser automation, or authenticated employer access.

Current status: [geography and professional relevance review](RELEVANCE_REVIEW.md). There are 30 verified employer boards; the latest run fetched 2,761 postings and retained 44 current records. The main list contains 23 records requiring geographic review, with zero confirmed Moldova-accessible vacancies. The dashboard supports New, Reviewed, Shortlisted, Rejected and Applied statuses in SQLite; repeated discovery preserves manual choices. Professional fit is separate from eligibility. See [Windows setup](WINDOWS_SETUP.md) and the private local ranked vacancy report.

## Windows setup (PowerShell)

Open a terminal in this project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Open http://127.0.0.1:8501. Keep `candidate_profile.json` locally; it is deliberately ignored by Git. If copying a tracked checkout, supply this private file separately using `agent.models.Candidate` as the schema. Create or restore this private file from your actual CV; no personal profile is shipped in Git. Unknown contact details, citizenship, work authorization and salary preferences stay unknown; no language proficiency is inferred. Optional local PDF extraction:

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

- Greenhouse: https://developers.greenhouse.io/job-board.html вЂ” `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true`
- Lever: https://github.com/lever/postings-api вЂ” `GET https://api.lever.co/v0/postings/{board}?mode=json`
- Ashby: https://developers.ashbyhq.com/docs/public-job-posting-api вЂ” `GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true`

`config/search_rules.yaml` controls title keywords, weights and request interval. Salary minimum is null: salary is preserved but never penalized, and its 15 points redistribute in proportion to 35:20:20:10. Non-null salary preferences deliberately raise a configuration error until currency/period-aware comparison is implemented. Unknown language proficiency receives neutral additional-requirement credit, never an assertion of proficiency.

Moldova is the candidate's only verified residence. EU countries (including Cyprus in the same category) and Romania are reviewable opportunities; EU residence/authorization is never assumed. Worldwide remote and explicit Moldova remote can qualify if no detected restrictions exist. Other locations, vague Europe/EMEA remote scopes, onsite relocation and unknown authorization need review. Country detection is conservative heuristic text processing, not a legal eligibility determination. Relevance filtering keeps Senior and Automation QA titles. Rule-based mandatory requirement detection intentionally favors review. All original descriptions are stored verbatim; HTML is rendered as plain text for safety. Missing structured requirements remain `unknown`; full descriptions are always available to matching.

## Private CV preparation

Keep the verified profile at `candidate_profile.json`; both it and all CV documents are Git-ignored. Build or refresh the general-purpose Master CV from those verified facts with:

```powershell
.\.venv313\Scripts\python.exe -m agent.cv_workflow --build-master
```

For a vacancy already marked Shortlisted, generate a tailored draft with its stable SQLite ID:

```powershell
.\.venv313\Scripts\python.exe -m agent.cv_workflow --job-id 4
```

The Master CV is saved to `private/cv/master_cv.md`; tailored drafts go under `private/application_pilot/`. The generator reads vacancy details from SQLite in read-only mode. It uses the profile as the source of candidate facts and the Master CV as the completeness check; the job description can change ordering and emphasis but cannot add qualifications or experience. Run the workflow tests with `python -m pytest tests/test_cv_workflow.py -q`.

## Scheduling on Windows

In Task Scheduler, create a task for the current user. Set a daily trigger and these action fields (replace the path):

- Program: `C:\path\job-agent\.venv\Scripts\python.exe`
- Arguments: `-m agent.cli discover`
- Start in: `C:\path\job-agent`

Choose вЂњDo not start a new instanceвЂќ in task settings. An OS-backed file lock also prevents concurrent dashboard/CLI searches. Logs are at `data/agent.log`; search outcomes/errors are persisted. Interrupted runs can remain marked Running in history; the OS releases the lock, allowing the next search. CLI exit codes: 0 success, 1 failed, 2 overlap, 3 partial (review warnings).

## Persistence and privacy

SQLite tables: candidate_profiles, companies, job_sources, jobs, job_observations, job_matches, search_runs, application_records (reserved, never submitted). Jobs retain first discovered and last checked dates. Latest source content is updated on rediscovery. Match history appends with candidate snapshot, job-description snapshot and logic/config version. Source identities and latest per-source payloads are retained in the additive job_observations table. Deduplication uses exact platform/board/source ID and canonical application URL. Different application URLs remain separate even when employer, title and location match; startup repair splits historical fuzzy groups and retains assessment history. Manual review statuses survive rediscovery. No automatic deletion or application occurs. Search history distinguishes fetched board postings from stored relevant vacancies.

Local profile, `.env`, PDFs, logs and databases are excluded from Git. Keep the local dashboard bound to 127.0.0.1. No credentials ship with the project. No fabricated live or demo results are used.

## Validation limits

Public source availability and vacancy contents change. Inaccessible boards are logged while other boards continue. Robots directives are checked before each board request, HTTP errors (including 429) stop that board without aggressive retry. Geographic matching is heuristic and cannot confirm work authorization. No removed-posting lifecycle tracking is implemented in MVP-01.

## Latest validation

See [RELEVANCE_REVIEW.md](RELEVANCE_REVIEW.md) for current live discovery, source evidence, full test results and limitations.

## Windows handoff and source readiness

Follow [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for installation, daily Task Scheduler setup and troubleshooting. [SOURCE_VERIFICATION.md](SOURCE_VERIFICATION.md) and [RELEVANCE_REVIEW.md](RELEVANCE_REVIEW.md) document the active verified boards and source limits. Run `python -m agent.cli verify-boards` only with permitted network access. It saves endpoint evidence locally without marking employer ownership verified. `python -m agent.diagnostics` checks profile and runtime readiness.

The dashboard defaults to rule-based matching; its OpenAI checkbox is opt-in. Region filters group Cyprus with EU countries and support geographic-priority ranking. A failed board transaction rolls back before processing the next board. Malformed postings are isolated with warnings. Logs from dashboard and CLI rotate in `data/agent.log`. The full runtime remains unverified in the managed environment.

See [DELIVERY.md](DELIVERY.md) before putting this source into a private Git repository. Keep the private profile separately; no repository was created or pushed in this environment.


## MVP-02 source verification status

[VERIFIED_SOURCES.md](VERIFIED_SOURCES.md) records the blocked API check. Verified boards: 0; active boards: 0. All three existing leads are disabled in `config/company_boards.unverified.yaml`. No real QA vacancies were retrieved. The active registry intentionally stays empty until sources pass API and employer-ownership verification. With permitted network access, explicitly check pending entries using `python -m agent.cli verify-boards --registry config/company_boards.unverified.yaml --include-disabled`; this does not activate them. Earlier starter-registry descriptions above are historical and are superseded by this status.
