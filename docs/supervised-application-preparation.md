# Supervised application preparation

Application preparation is available only for vacancies already marked `Shortlisted` and only when a private tailored PDF has matching `job_id` and exact `application_url` metadata. Preparation opens a visible Microsoft Edge window and checks the employer's robots policy before visiting the official ATS page.

Greenhouse uses a reusable form adapter for the supported official Greenhouse hosts. It fills uniquely identified fields backed by verified candidate facts, uploads the matched PDF, and captures a private preview and screenshot. Required employer-specific questions, unclear location fields, work authorization, salary, consent, and legal declarations remain for candidate review unless an exact vacancy-scoped answer has already been confirmed for that role. The submit event is guarded and submit buttons are disabled.

Lever preparation uses the official Lever posting host. It fills labeled identity fields and unambiguous current-residence fields, uploads the matched PDF, and records remaining required or sensitive questions for review. It does not submit applications or accept consent/legal answers.

Newly supported Greenhouse employers are preparation-only. Their preview records `submission_enabled: false`; they cannot enter the existing submission approval flow. Live submission remains restricted to its separately reviewed Bitdefender pilot and requires the existing application-specific approval safeguards. Preparation never changes a vacancy review status.

Examples from the project root:

```powershell
.\.venv313\Scripts\python.exe -m agent.application_workflow --job-id 40 --platform greenhouse --headed
.\.venv313\Scripts\python.exe -m agent.application_workflow --job-id 13 --platform lever --headed
```

Candidate profiles, PDFs, application metadata, previews, screenshots, and application state are stored under ignored private paths. LinkedIn requests are blocked by the browser route guard; CAPTCHA, authentication challenges, robots denials, and off-host redirects stop preparation.
