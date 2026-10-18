> Historical report. Current takeover status supersedes this document: [TAKEOVER_VALIDATION.md](TAKEOVER_VALIDATION.md) and [SOURCE_CHECK_2026-10-18.md](SOURCE_CHECK_2026-10-18.md). Private candidate data is never part of the repository.

# Employer source verification

Current independently verified board count: **0**. No live discovery succeeded in the managed environment. The previous sandbox socket denial remains the blocker; this development pass made no further network attempts.

| Existing starter entry | Platform / identifier | Status and follow-up |
| --- | --- | --- |
| Grafana Labs | Greenhouse / `grafanalabs` | Pending: confirm official employer link, endpoint, current QA vacancies and country scope. |
| PostHog | Ashby / `posthog` | Pending: confirm official employer link, endpoint, current QA vacancies and Moldova/European hiring scope. |
| Wealthsimple | Lever / `wealthsimple` | Pending: confirm official employer link and endpoint. Existing collector example; Moldova/EU hiring is not established. |

No additional identifiers were added without evidence. Starter entries are disabled in `config/company_boards.unverified.yaml`, with `verification_status: pending`. The active registry is empty. They are not claims of current QA openings. An employer's past global remote policy does not prove a particular vacancy accepts Moldova residents.

When network access is permitted, use `python -m agent.cli verify-boards --registry config/company_boards.unverified.yaml --include-disabled`. It checks robots policy and each public board, writing `data/board_verification.json`. Accessible output confirms API access/normalization only. Manually verify ownership through the official employer careers page before updating registry evidence fields. A malformed item is skipped with warnings; one failed board does not prevent checking the others.

Prioritize employers with documented Moldova/Romania locations, EU offices, then international remote employers explicitly accepting Moldova. Use their own careers links to determine actual Greenhouse/Lever/Ashby board identifiers. Only document verified identifiers; do not convert company names into guessed slugs. This is a registry, not a global search API.

Public endpoint documentation:

- https://developers.greenhouse.io/job-board.html
- https://github.com/lever/postings-api
- https://developers.ashbyhq.com/docs/public-job-posting-api

The collector checks API-origin robots policy before reading each board, respects crawl delay, pauses between requests and stops on HTTP errors. Endpoint access does not establish every employer-specific reuse policy; review it before adding sources. This MVP does not scrape employer career pages or automate LinkedIn.


MVP-02: see [VERIFIED_SOURCES.md](VERIFIED_SOURCES.md) for the exact failed API attempt, unknown source metrics, inactive registry and Windows continuation. No sources were verified.
