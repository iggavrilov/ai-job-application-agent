> Historical report. Current takeover status supersedes this document: [TAKEOVER_VALIDATION.md](TAKEOVER_VALIDATION.md) and [SOURCE_CHECK_2026-10-18.md](SOURCE_CHECK_2026-10-18.md). Private candidate data is never part of the repository.

# MVP-02 — Verified employer sources

Verification attempt: **2026-10-16 06:01:18 UTC**.

## Outcome

- Independently verified employer boards: **0**.
- Active boards in `config/company_boards.yaml`: **0**.
- Disabled pending sources in `config/company_boards.unverified.yaml`: **3**.
- Real QA-related vacancies retrieved in this attempt: **0**. Actual vacancy totals are **unknown**, not confirmed zero.
- Target of at least 20 verified boards: **not achieved; network blocked**.

No employers, board identifiers, job openings, careers-page ownership, HTTP successes or vacancy counts were invented. No salary thresholds were applied. No LinkedIn or application submission workflow was used.

## Exact blocker and scope of investigation

A single HTTPX request used the inherited proxy, TLS verification and an eight-second timeout:

```text
GET https://boards-api.greenhouse.io/v1/boards/grafanalabs/jobs?content=true
Checked at: 2026-10-16T06:01:18.285387+00:00
ConnectError: [Errno 1] Operation not permitted
HTTP status: none (no response received)
```

The managed environment reports its cloud HTTP policy as unrestricted/enforced, but the execution sandbox denies the socket connection. This is a local execution/network-permission failure, not a Greenhouse HTTP rejection and not evidence that the board exists or is unavailable. No employer payload or HTTP status was received. On this first failure, live verification stopped. Lever, Ashby and official careers-page research were not attempted after the blocker. No proxy bypass, alternate network route, escalation, package installation or repeated retry was attempted.

## Source ledger — all unverified and inactive

The following identifiers were already present in the project. They are retained as research leads only; they are not new findings or verified identifiers.

| Company | Claimed ATS | Existing identifier (unverified) | Official careers URL | Verification attempt (UTC) | API HTTP status | Currently publishes jobs? | Published-job count | QA-related vacancies |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Grafana Labs | Greenhouse | `grafanalabs` | Unknown; ownership research blocked | 2026-10-16 06:01:18 | None; socket denied | Unknown | Unknown | Unknown; none retrieved |
| PostHog | Ashby | `posthog` | Unknown; ownership research blocked | Not attempted after network failure | Not observed | Unknown | Unknown | Unknown; none retrieved |
| Wealthsimple | Lever | `wealthsimple` | Unknown; ownership research blocked | Not attempted after network failure | Not observed | Unknown | Unknown | Unknown; none retrieved |

Pending API URLs (not verified):

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/grafanalabs/jobs?content=true`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/posthog?includeCompensation=true`
- Lever: `https://api.lever.co/v0/postings/wealthsimple?mode=json`

No current Moldova, Romania, EU or worldwide hiring coverage can be asserted for these employers. Wealthsimple is retained solely as an existing source lead; regional suitability remains unestablished. Geographic eligibility must be evaluated per vacancy, even after board ownership/access is verified. Cyprus remains within the EU category.

## Registry changes

`config/company_boards.yaml` now contains `boards: []`. The three existing entries moved to `config/company_boards.unverified.yaml` with `enabled: false` and `verification_status: pending`. Discovery only reads the active registry. With no verified sources it will report a failed search with “No valid enabled boards configured,” rather than quietly using unverified sources or fabricating jobs.

No architecture refactor was performed. The existing verification command gained only explicit registry/disabled-source options and response metadata needed for a later real check. The collector exposes actual response status and response posting count to that command. Counts and QA titles are written only after a real successful response (or explicitly mocked tests), never from guessed data.

## Practical Windows continuation

On a Windows PC with permitted internet access, follow WINDOWS_SETUP.md to create the venv and install requirements. Do not run discovery until at least one source is confirmed.

1. Research employer-owned careers pages, prioritizing Moldova, Romania, EU offices and remote employers explicitly accepting candidates in these countries. Follow their Greenhouse/Lever/Ashby links. Add the exact observed identifier to the disabled queue, not to the active registry. Record the official careers URL in `evidence_url`. Do not derive slugs from company names.
2. Verify the disabled queue explicitly:

```powershell
Set-Location C:\Projects\job-agent
.\.venv\Scripts\python.exe -m agent.cli verify-boards --registry config/company_boards.unverified.yaml --include-disabled
Get-Content data/board_verification.json
```

This command respects robots policy and does not enable sources. It records company, platform, identifier, supplied official careers URL (null if not researched), check timestamp, HTTP status when observed, response posting count, whether the response has postings, and QA title/location/URL records. Invalid postings generate warnings. A successful endpoint does not establish employer ownership or Moldova eligibility. A response count is a snapshot of that API response, not a count of every employer opening across every hiring channel. If a request is blocked before the board API is reached, endpoint HTTP status remains null.

3. For each successful board, confirm the public careers page actually links to that employer board and the returned jobs belong to the employer. Confirm the reported QA titles are software-testing roles and inspect their country/authorization restrictions. Empty accessible boards can be recorded as verified with zero postings if ownership is established. Do not claim any particular residency eligibility based only on employer name or remote branding.
4. Copy only confirmed entries to `config/company_boards.yaml`, setting `enabled: true`, `verification_status: verified`, `verified_at` to the actual successful check's ISO UTC timestamp, and `evidence_url` to the confirmed official careers page. Remove those entries from the pending queue. Keep unsuccessful/unresearched entries disabled. Keep full status/count evidence in the local JSON report and update this source ledger from observed values.
5. Repeat employer research and verification until 20 boards are genuinely confirmed; do not pad the registry if fewer suitable sources exist.
6. Run full tests, then real discovery and the dashboard:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m agent.cli discover --no-ai
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Do not publish private `.env`, candidate profile, database or verification artifacts containing future private notes. No OpenAI key is required for public source verification or rule-based matching.

## Executed checks

- Existing available-environment offline tests: **14 passed**.
- Existing reliability offline tests: **18 passed**.
- Total: **32 passed, 0 failed**, including mocked API normalization and failed-source continuation.
- Syntax compilation: passed.
- Full pytest: unavailable because pytest is not installed. SQLAlchemy, filelock, Streamlit and OpenAI are still unavailable; no installation was retried.
- Database integration, dashboard runtime, live API ingestion and Windows execution remain unverified.

The registry tests now assert that the active registry is empty and all three research entries are disabled. Mock verification explicitly selects the disabled queue; it never changes live source status. The existing database discovery test uses independent in-memory board fixtures rather than depending on production registry entries.

## Files changed for MVP-02

Created: `VERIFIED_SOURCES.md`, `config/company_boards.unverified.yaml`.

Modified: `config/company_boards.yaml`, `agent/cli.py`, `agent/collectors.py`, `tests/test_reliability_offline.py`, `tests/test_agent.py`, `WINDOWS_SETUP.md`, `SOURCE_VERIFICATION.md`, `README.md`, `VALIDATION.md`.
