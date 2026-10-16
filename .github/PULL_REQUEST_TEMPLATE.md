<!--
The PR title becomes the squash-commit message and drives the release version, so it must follow
Conventional Commits: feat: ..., fix: ..., docs: ..., feat!: ... (breaking). See CONTRIBUTING.md.
-->

## What and why

<!-- What does this change, and what problem does it solve? Link the issue: Fixes #123 -->

## How it was tested

<!-- Tests added/updated, and where you ran it (Windows / Linux desktop / which board + compositor). -->

## Checklist

- [ ] `ruff check .` and `python -m pytest` pass
- [ ] Docs updated (`README.md` flag table, `docs/API.md` for control/JS API changes)
- [ ] Still runs on Python 3.9 (the embedded arm64 build)
