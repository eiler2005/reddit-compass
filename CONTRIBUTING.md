# Contributing to reddit-compass

Thank you for considering a contribution. This document explains the workflow.

## Development Setup

```bash
git clone https://github.com/eiler2005/reddit-compass.git
cd reddit-compass
uv sync --dev
uv run playwright install chromium
```

## Quality Gate (must pass before commit)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src          # strict mode
uv run pytest            # 60% coverage minimum
scripts/secret-scan --all # repo-specific IP/host/secret scan
```

Pre-commit hooks run automatically on `git commit`:
- ruff (lint + format)
- check-json / check-toml / check-yaml
- repo-local secret scan
- detect-private-key
- **detect-secrets** (blocks commits with leaked API keys/tokens)

## Commit Convention

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- One logical change per commit
- Explicit staging only — **no `git add .` or `git add -A`**
- Never commit secrets or production endpoints (`.env`, tokens, keys, public deploy IPs/hosts)

## Architecture Rules

Read [`AGENTS.md`](AGENTS.md) before contributing. Key boundaries:

- **Read-only** — the service never posts, votes, or comments on Reddit
- **Config-driven** — behavior changes via `config/profiles/*.json`, not code
- **JSONL as exchange format** — consumers read files, not runtime
- **No external imports** — the service is fully autonomous
- **Secrets in `.env.secrets`** — never in code, tests, or docs

## Adding a New Source

1. Create `src/reddit_compass/sources/<name>.py`
2. Implement `fetch_<name>()` → returns `list[PostCard]`
3. Add CLI command in `cli.py`
4. Add to coverage omit in `pyproject.toml` (network module)
5. Update `docs/MULTI_SOURCE_PLAN.md`
6. Add test (mock network, test parsing)

## Pull Requests

1. Fork → branch → commit → PR
2. CI must pass (ruff + mypy + pytest)
3. No `scripts/secret-scan` or `detect-secrets` alerts
4. Update CHANGELOG.md under `[Unreleased]`

## Questions?

Open an issue. We respond.
