# Supervised application workflow

The workflow prepares shortlisted Greenhouse applications in visible Microsoft Edge and keeps application states separate from vacancy review statuses. Greenhouse is supported first; Lever remains unsupported. Application state is stored in Git-ignored JSON under `private/application_runs/` and never changes the SQLite vacancy review status automatically.

## Prepare the Bitdefender pilot

Install project dependencies in the local virtual environment, including Playwright. Microsoft Edge must already be installed. Restore the real, Git-ignored `candidate_profile.json` and the private tailored PDF at `private/application_pilot/job-4-bitdefender-qa-engineer/cv.pdf`.

From the dashboard, select Bitdefender vacancy ID 4, confirm it is Shortlisted, then choose **Prepare Application**. The browser opens the official Greenhouse page. The command-line equivalent is:

```powershell
.\.venv313\Scripts\python.exe -m agent.application_workflow --job-id 4 --headed
```

The browser remains open for inspection until it is closed. A local private preview is saved at `private/application_runs/job-4/application_preview.json`, with its screenshot beside it. The preview includes the DOM-verified form values, candidate identity, application URL, attached CV name and fingerprint; these generated artifacts are excluded from Git. Candidate-confirmed form preferences are loaded from the Git-ignored `private/application_preferences.json`; keep that file local.

## Field handling and safety

Greenhouse text fields, dropdowns, radio controls, checkboxes and resume file inputs have explicit typed mapping support. The workflow reads candidate-confirmed name, preferred name, residence and salary preferences from the Git-ignored `private/application_preferences.json`. It verifies text values in the live DOM and attaches the tailored PDF.

Location mapping follows the field wording: current-city and residence fields use the configured private residence; explicitly preferred job locations use the preference for the stated country. A generic `Location (City)` field stays blank unless a candidate answer is explicitly scoped to that vacancy. Salary, work-authorization and consent answers are populated only when explicitly confirmed for that exact application URL. Bitdefender’s location is Bucharest while Moldova-based remote eligibility is not explicit, so preparation remains provisional and the employer must confirm eligibility. Romanian work authorization alone is not treated as proof of permission to work remotely from Moldova.

## Candidate-approved submission

After a successful preparation with no unanswered required fields, the private application state becomes **Ready for Approval**. The dashboard then displays the exact employer, role, URL, candidate identity and residence, application location, salary, legal and consent answers, attached CV filename, and a fingerprint of the reviewed data.

To submit, the candidate must check the confirmation for that exact vacancy and URL and click **Approve & Submit**. This is a fresh, one-use approval. The workflow binds it to the vacancy ID, application URL, verified form snapshot, answer set, candidate data, and CV SHA-256. It reopens the official Greenhouse page, fills and verifies the form again, and submits only if the live snapshot still matches. Changed candidate data, answers, URL, form values, or CV invalidates the review and requires preparation again.

The workflow records **Prepared**, **Needs Review**, **Ready for Approval**, **Approved**, **Submitting**, **Submitted**, **Submission Failed**, or **Submission Uncertain** in private application state. These states do not overwrite New, Reviewed, Shortlisted, Rejected, or Applied in SQLite. A process interrupted during submission is treated as uncertain and cannot retry automatically. If Greenhouse does not show a clear confirmation, check the employer site manually before taking any further action.

CAPTCHA, bot protection, authentication challenges, redirects outside official Greenhouse hosts, unrecognized controls, and empty required fields stop submission. A submission is recorded as Submitted only when the browser remains on an official Greenhouse host, shows a recognized confirmation message, and the application form is no longer visible. Any other post-click result is Submission Uncertain and cannot be retried automatically. The submit control stays disabled until the exact approval is checked against the live form. LinkedIn hosts and forms are not supported.

After reviewing the private preview, close the preparation browser. The vacancy remains Shortlisted. Submission runs only after the candidate reviews the dashboard snapshot and gives a fresh, application-specific approval.
