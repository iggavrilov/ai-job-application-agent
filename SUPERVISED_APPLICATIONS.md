# Supervised application preparation

Phase 1 prepares selected Shortlisted Greenhouse vacancies in a visible Microsoft Edge window. It does not submit forms or change vacancy review statuses. The adapter interface is provider-specific; Greenhouse is implemented first, and Lever remains unsupported.

## Prepare the Bitdefender pilot

Install project dependencies in the local virtual environment, including Playwright. Microsoft Edge must already be installed. Restore the real, Git-ignored `candidate_profile.json` and the private tailored PDF at `private/application_pilot/job-4-bitdefender-qa-engineer/cv.pdf`.

From the dashboard, select Bitdefender vacancy ID 4, confirm it is Shortlisted, then choose **Prepare Application**. The browser opens the official Greenhouse page. The command-line equivalent is:

```powershell
.\.venv313\Scripts\python.exe -m agent.application_workflow --job-id 4 --headed
```

The browser remains open for inspection until it is closed. A local private preview is saved at `private/application_runs/job-4/application_preview.json`, with its screenshot beside it. These generated artifacts are excluded from Git.

## Field handling and safety

Greenhouse text fields, dropdowns, radio controls, checkboxes and resume file inputs have explicit typed mapping support. The live pilot fills first name, last name, email, country, phone and city from verified profile facts, then attaches the tailored PDF. Preferred first name is left blank for candidate input because a preferred name is not separately confirmed. The supported mapping is intentionally narrow; no field is filled from a job-description prompt or guessed answer.

Work-authorization declarations, salary expectations, data-processing consent and any unknown employer questions are left unanswered and listed in the preview. Bitdefender’s location is Bucharest while Moldova-based remote eligibility is not explicit, so preparation is provisional and the employer must confirm eligibility. Romanian work authorization alone is not treated as proof of permission to work remotely from Moldova.

The final submit button is disabled and a capture-phase handler blocks form submission, including keyboard submission. No submission action exists in this phase. Do not manually re-enable or bypass the guard. CAPTCHA, bot protection, login challenges, redirects outside official Greenhouse hosts, and unrecognized controls stop automation for manual review. LinkedIn hosts and forms are not supported.

After reviewing the private preview, close the browser. The vacancy remains Shortlisted. A future submission phase would need a separate explicit candidate approval step and confirmation of all legal, consent, salary and unanswered fields.
