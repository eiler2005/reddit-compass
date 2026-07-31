# Secret scanning

reddit-compass uses two layers before commit/push:

1. `scripts/secret-scan` — repo-local scanner for project-specific leaks:
   deployment IPv4 addresses, `sslip.io` hosts with embedded IPs, private-key markers,
   bearer tokens, API-key-like values and non-placeholder secret assignments.
2. `detect-secrets` + `detect-private-key` — generic pre-commit hooks.

Run manually before pushing (this scans exactly the staged commit payload):

```bash
scripts/secret-scan
uv run pre-commit run --all-files
```

For an explicit whole-working-tree audit, use `scripts/secret-scan --all`. It is
not the pre-commit default: unrelated work may legitimately be present while a
small, independent fix is being committed.

Rules:

- Keep real hosts, IPs, passwords, API keys and Basic Auth values only in gitignored files:
  `.env`, `.env.*`, `deploy/hostkey/.env.secrets` or SSH config.
- Tracked docs may use placeholders such as `<public-url>`, `<client-secret>` and
  `example.invalid`.
- Do not store production URLs with embedded public IPs in generated reports.
- If a real secret ever lands in git history, rotate it first, then clean history with BFG or an
  equivalent tool.

The scanner intentionally ignores gitignored runtime data and secrets. The hook
reads staged blobs from Git's index; `--all` reads tracked and non-ignored
untracked worktree files.
