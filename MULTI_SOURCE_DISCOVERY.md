# Multi-source QA job discovery

The discovery pipeline combines verified employer ATS boards with a small set of documented public feeds. Each record keeps the source listing URL separate from an employer application URL when the provider actually supplies one. Jobs from aggregators retain provider attribution; for Himalayas, Apply remains the provider-specified `applicationLink`. Matching and geographic eligibility are separate. Uncertain location or authorization evidence stays reviewable; EU onsite/hybrid roles can be classified `Eligible If Relocating` when EU work authorization is verified and relocation remains unconfirmed.

## Sources and access policy

The full registry, including source type, coverage, official URL, evidence URL, terms, authentication, permitted access, rate limits, current health fields, and integration status, is `config/discovery_sources.yaml`. Existing employer-specific board IDs and their official verification evidence remain in `config/company_boards.yaml`; the source registry does not substitute guessed board tokens for those verified records.

Integrated public APIs are Arbeitnow, Remote OK, and Himalayas. Greenhouse, Lever, and Ashby public-board adapters operate only on configured verified employer boards; there are currently no verified Ashby employer board IDs. Official employer careers pages are supported through those ATS boards. Lever supervised preparation fills only unambiguous candidate identity fields and a matching private CV; unknown legal, salary, location, and consent questions remain manual. Lever submission is disabled.

Remotive, EURES, Just Join IT, No Fluff Jobs, Landing.jobs, Welcome to the Jungle, EuroTechJobs, eJobs, BestJobs, Hipo, DevJob.ro, Rabota.md, Delucru.md, Joblist.md, Working Nomads, We Work Remotely, Wellfound, Jobgether, Built In, Dice, Indeed, Workday, SmartRecruiters, Teamtailor, Recruitee, BambooHR, Personio, iCIMS, Oracle Recruiting, SAP SuccessFactors, and Workable are registered as research-only until their exact public access method, robots policy, terms, and applicable attribution have been verified. No login, blocked endpoint, scraping workaround, or credentialed API is used.

The Himalayas public feed uses 20 jobs per cursor page, a two-second interval, and a 50-page safety cap because anonymous requests share an unspecified allowance. A safety-cap stop is marked partial; the UI never claims a complete source walk in that case. Arbeitnow uses documented page links with a 100-page cap. Remote OK is a single public feed and is fetched once per discovery run. HTTP 429 stops the provider scan without retry. All API requests are gated on robots.txt.

## LinkedIn handling

The manual import accepts job URLs or pasted alert/export text supplied by the user. It parses metadata locally and stores only the URL, title, employer, location, explicitly supplied employer application URL, reconciliation state, and a possible exact match against the local verified ATS inventory. It does not retain the pasted message body or sender details. It never requests a LinkedIn URL. Imported leads remain `Needs Official Verification` until matched to a real employer posting; discovery can reconcile them against the local official ATS inventory.

## Job lifetime and identity

Source job IDs and canonical URLs identify observations. Distinct source IDs and distinct official application URLs remain separate even when employer and title match. Provider expiry dates are recorded. A job missing from a **complete** source scan is labeled `Not observed in latest complete source scan`; that is not evidence of closure and never deletes the job or changes its manual review status. Partial scans never mark missing jobs. The additive `job_source_status` SQLite table is created with SQLAlchemy `create_all`; no destructive migration is used.

## Running

From PowerShell in the project root:

```powershell
.\.venv313\Scripts\Activate.ps1
python -m agent.diagnostics
python -m agent.cli discover --no-ai
streamlit run app.py
```

Source health and scan diagnostics appear in the dashboard. Current-run counts distinguish provider postings, malformed entries, expired entries, title filtering, eligibility classifications, and pagination completion. SQLite review statuses are preserved by upsert. Private profiles, CVs, application previews, generated documents, credentials, and databases remain ignored by Git.
