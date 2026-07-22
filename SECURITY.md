# Security Policy

## Threat Model

reddit-compass is a **single-operator, read-only** data collection service.
It collects public data from 21 sources and serves it via a local API.

### Assets

| Asset | Location | Sensitivity |
|---|---|---|
| API keys (DASHSCOPE, TELEGRAM, RC_API_SECRET) | `.env.secrets`, VPS `.env` | High |
| Collected data (JSONL, SQLite) | `data/`, VPS volume | Low (public data) |
| SSH key (VPS access) | `~/.ssh/id_rsa` | High |
| JWT signing secret | `RC_API_SECRET` env | Medium |

### Threats & Mitigations

| Threat | Mitigation |
|---|---|
| Secret leak via git | `detect-secrets` pre-commit + `detect-private-key` + gitignore |
| Unauthorized API access | OAuth2 client credentials → JWT (1h expiry) |
| Network exposure | Loopback only (127.0.0.1), Caddy reverse proxy, no public ports |
| Container escape | `read_only`, `no-new-privileges`, `cap_drop: ALL`, `pids_limit` |
| Reddit ToS violation | Read-only, rate-limited, public data, no posting/voting |
| Proxy abuse | Proxy only for 429 mitigation, never for ban evasion |
| Supply chain (pip) | `uv.lock` (locked dependencies), CI builds from lock |

### What the service NEVER does

- Post, vote, comment, or authenticate as a user on any platform
- Bypass account bans or blocks (proxy is for rate limits only)
- Train ML models on collected content
- Expose secrets in logs, API responses, or error messages
- Touch other services on the VPS (isolated network + volume)

## Reporting a Vulnerability

If you find a security issue:

1. **Do NOT open a public issue**
2. Email: denis.ermilov@gmail.com
3. Include: description, reproduction steps, impact assessment
4. We will acknowledge within 48 hours and fix within 7 days

## Secret Rotation

If a key is compromised:

1. Rotate immediately at the provider (Qwen Cloud, Telegram, etc.)
2. Update `.env.secrets` locally and on VPS
3. If committed to git history: `git filter-branch` or BFG Repo-Cleaner
4. Force-push cleaned history (coordinate with collaborators)

## Dependencies

- Locked via `uv.lock` — no floating versions in production
- CI runs on every push: ruff + mypy strict + pytest + detect-secrets
- Docker images built from locked dependencies
