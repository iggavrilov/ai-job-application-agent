> Historical report. Current takeover status supersedes this document: [TAKEOVER_VALIDATION.md](TAKEOVER_VALIDATION.md) and [SOURCE_CHECK_2026-10-18.md](SOURCE_CHECK_2026-10-18.md). Private candidate data is never part of the repository.

# Private project delivery and Git preparation

The existing project was updated in place. Source is prepared for review and a local Git commit, but is **not a fully validated release**. Full dependency installation, pytest, Windows startup and live discovery remain acceptance gates. No repository was created, no commit made, and nothing was pushed.

The workspace `.git` entry is an empty, read-only placeholder, not a working Git repository (`git status` reports not a repository). No GitHub connector/credentials are configured for this work. A private GitHub repository has not been provisioned. No GitHub installation or public repository is assumed.

Ignored local artifacts include `.env` and `.env.*` except `.env.example`, `candidate_profile*.json`, all PDFs, `data/`, SQLite variants/sidecars, logs, private/cv folders, virtual environments, caches, common key and credential filenames. The current private profile stays available locally and is not part of the proposed Git source. Keep a separate private backup: cloning the source will not restore it. `.gitignore` is not encryption and cannot protect files already tracked or force-added.

When at the PC, copy source and private profile using private storage. Do not transfer the Linux `.venv`; build a new Windows venv. Follow WINDOWS_SETUP.md and run tests first. To initialize a new local repository only if the copied folder is not already in one:

```powershell
Set-Location C:\Projects\job-agent
git init
git check-ignore candidate_profile.json .env data/jobs.db
git add .
git diff --cached --name-only
git diff --cached --stat
```

Review the staged filenames and source before committing. The candidate profile, CV PDFs, environment secrets and database must not appear. If one appears, unstage it with `git rm --cached -- <path>` (or `git restore --staged -- <path>` in a repository with a commit), retaining the local copy. Do not use `git add -f` for private files. Review existing tracked files in a preexisting repository because ignore rules do not untrack them. Then make a local commit if desired:

```powershell
git commit -m "Prepare QA discovery MVP and Windows setup"
```

Do not add a remote or push until a private repository and its access are explicitly configured. No credentials or personal information should be committed even to a private repository. The source tests require your private profile locally; do not add that file to CI artifacts or repository fixtures.
