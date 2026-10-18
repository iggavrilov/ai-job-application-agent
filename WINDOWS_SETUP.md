# Windows setup and daily discovery

The project is prepared for Windows Python 3.12+, but has not been executed on Windows. Offline checks passed in the managed Linux environment. Full pytest, SQLite runtime, Streamlit startup and live sources still need verification. No application is submitted by any command.

## 1. Prerequisites and private files

Install 64-bit Python 3.12 or later from python.org with the Python launcher. Git is optional. Use a normal local folder, for example `C:\Projects\job-agent`, with write access. An internet connection must permit your package index and the three public API domains. Do not disable TLS verification or bypass a company proxy.

Copy the existing project into that folder, including your local private `candidate_profile.json`. That file is deliberately excluded from Git: a Git checkout alone will not contain it. Keep the delivered profile separately in private storage; never paste the CV or personal profile into a repository issue. The current profile uses supplied facts; the PDF was unavailable for independent verification. If the profile is missing, restore your private copy before discovery. The schema is `agent.models.Candidate`.

## 2. Install (PowerShell)

Use these commands in order. Stop if a command fails; do not continue to later verification steps until it is resolved.

```powershell
Set-Location C:\Projects\job-agent
py -0p
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m agent.diagnostics
.\.venv\Scripts\python.exe -m pytest -q
```

Activation is unnecessary, so PowerShell execution-policy changes are unnecessary. If you use Python 3.13+ instead of 3.12, change the launcher selector accordingly. Do not copy the Linux `.venv` onto Windows; recreate it with the command above.

## 3. Environment variables and configuration

Edit local `.env`, never `.env.example`, for secrets:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Optional. Leave empty for rule-based matching. |
| `OPENAI_MODEL` | Optional model with structured JSON support; defaults to `gpt-4o-mini`. |
| `DATABASE_URL` | Optional SQLite URL; default uses `data/jobs.db`. Relative database paths resolve from the project folder. |

`config/search_rules.yaml`: title include/exclude keywords, score weights, request interval. Keep `minimum_salary: null`; missing/low salary never reduces compatibility. Salary is preserved when supplied. Technical/experience/geography/additional weights receive the redistributed salary weight.

`config/company_boards.yaml`: employer, source platform (`greenhouse`, `lever`, `ashby`), board identifier, enabled flag and verification notes. The active registry is empty. Three existing starter entries are disabled in `config/company_boards.unverified.yaml`. **No boards are independently verified yet.** The registry is intentionally small; add employers only after finding the actual board link on their official careers site. A successful API response alone does not establish employer ownership or Moldova eligibility.

For Moldova/Romania/EU coverage, verify actual employer careers pages first, then confirm the country terms in each vacancy. Never guess a board slug from a company name. All EU countries, including Cyprus, share the EU region. Remote Europe or EU-only does not establish eligibility for a Moldova resident.

## 4. Verify sources, then discover

With permitted internet access:

```powershell
.\.venv\Scripts\python.exe -m agent.cli verify-boards --registry config/company_boards.unverified.yaml --include-disabled
```

The report prints results and is saved privately to `data/board_verification.json`. Review each endpoint, company ownership and warnings. The command does not update the registry automatically. After successful API verification and confirming ownership via the official employer careers page, copy that entry to the active registry with `enabled: true` and set `verification_status: verified`, `verified_at` to the actual UTC check time, and `evidence_url` to that careers page. Keep irrelevant/unavailable entries disabled in the separate queue. Until a verified source is activated, discovery intentionally reports no enabled boards. Accessible boards can have zero QA vacancies; that is not an error. This command checks access and normalization, not eligibility or database persistence.

```powershell
.\.venv\Scripts\python.exe -m agent.cli discover --no-ai
```

Inspect the result and search errors. `fetched` counts successfully normalized board postings; `new_relevant` counts newly stored relevant jobs after deduplication. Malformed entries produce warnings and a Partial run. Sources failing HTTP/robots/normalization or database writes are isolated. No failed board writes are counted as successful. Source records update only when fetched, so a job's last-checked timestamp does not mean the vacancy is still open.

To opt into OpenAI, set the API key and remove `--no-ai`. Professional profile facts and job descriptions are sent to OpenAI; candidate email/phone are excluded, and the full CV is never sent. No candidate data goes to employer public board APIs. OpenAI errors fall back to rules.

## 5. Start the dashboard

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Open http://127.0.0.1:8501 on that PC. Keep the terminal open. Stop with Ctrl+C. The dashboard is local to your PC; these instructions do not expose it to your phone or the internet.

Use “Discover jobs now” to run a search. OpenAI is off in the dashboard by default; opt in with its checkbox. Filter by region/location, remote status, title, score, recommendation, source and discovery date. Rank by score or geographic priority. “New” means not reviewed. Select a job for details; mark Interested, Not Interested or Reviewed. Original posting links open the employer site; no submission occurs. Unknown eligibility and uncertain mandatory requirements remain Needs Review regardless of score.

## 6. Schedule daily searches

First run a manual CLI search successfully. In Windows Task Scheduler, choose Create Task:

1. General: use your Windows user account. No administrator privileges are normally needed. Choose “Run only when user is logged on” initially for easier troubleshooting.
2. Trigger: Daily, at your preferred time (for example 09:00).
3. Action — Start a program:
   - Program/script: `C:\Projects\job-agent\.venv\Scripts\python.exe`
   - Add arguments: `-m agent.cli discover --no-ai`
   - Start in: `C:\Projects\job-agent`
4. Settings: “If the task is already running: Do not start a new instance.” Review battery/network conditions if using a laptop. “Run task as soon as possible after a scheduled start is missed” is optional.
5. Run the task manually and inspect Last Run Result plus `data/agent.log` and dashboard search history.

The discovery command also uses an OS-backed lock shared with the dashboard. The machine must be awake and connected. Running with a different Windows user requires that user's access to the project, `.env`, private profile and writable database. The dashboard need not be open.

CLI discovery exit codes: 0 = success, 1 = failed/no working boards, 2 = overlapping search, 3 = partial result (review warnings). A crash releases the file lock; its history entry can remain Running. Review logs before assuming a daily task ran successfully.

## 7. Troubleshooting

- **`py` not found:** install the Python launcher or use the full path to an installed Python 3.12+ executable. Open a new terminal after installation.
- **Wrong Python / module not found:** use the exact `.venv\Scripts\python.exe` commands above. `python -m agent.diagnostics` describes whichever interpreter you invoke. An empty venv does not inherit installed system packages.
- **ProxyError / Operation not permitted / timeout:** package access is blocked at the network/sandbox/proxy layer. Stop retries and use an authorized machine/network, or ask its administrator for permitted access. A following “No matching distribution” can be secondary to the network error. Do not disable TLS or bypass access controls.
- **Certificate verification failure:** obtain the approved corporate CA/proxy configuration from your administrator; do not add `--trusted-host` as a workaround.
- **Actual package incompatibility:** check `py --version`, use supported 64-bit Python, and read the original resolver error. Prefer Python 3.12 if a newer release has no matching binary wheels.
- **PowerShell scripts disabled:** do not activate; call `.venv\Scripts\python.exe` directly.
- **Profile missing/invalid:** restore your private `candidate_profile.json`, encoded UTF-8, and validate against `Candidate`. Git intentionally excludes it. Unknown information must stay unknown.
- **No jobs:** inspect enabled boards, source failures, verification report and keywords. No QA vacancies on a board is valid; no demo vacancies are inserted. Expand the registry using verified employer links.
- **HTTP 403/404/429 or robots policy failure:** keep the failure recorded, respect the source policy and rate limits, disable or correct that board, and continue with other sources. The collector does not bypass restrictions or aggressively retry.
- **Database locked:** close other processes that edit the database; the connection waits up to 30 seconds. Back up the `data` folder before changing versions. New source-observation tables are created additively; existing job/match tables are not dropped.
- **Search already running:** wait for the running CLI/dashboard search. Do not delete a lock file while a search is active.
- **Port 8501 occupied:** stop the previous Streamlit process or add `--server.port 8502`, retaining `--server.address 127.0.0.1`.
- **AI failure:** leave the key empty/use `--no-ai`; matching remains rule-based. Logs exclude SDK exception bodies that could contain submitted profile data.

## Acceptance on your PC

Do not mark MVP-01 accepted until the full pytest suite passes, a permitted live board returns real vacancies, SQLite persists/deduplicates relevant jobs on repeated discovery, and the local dashboard displays job details and original links. If the starter boards have no relevant jobs, verify/add a suitable employer board rather than inserting fabricated results. No LinkedIn or job-submission workflow is present.
