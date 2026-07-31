# Secret scanning

reddit-compass uses two layers before commit/push:

1. `scripts/secret-scan` — repo-local scanner for project-specific leaks:
   deployment IPv4 addresses, `sslip.io` hosts with embedded IPs, private-key markers,
   bearer tokens, API-key-like values and non-placeholder secret assignments.
2. `detect-secrets` + `detect-private-key` — generic pre-commit hooks.

Run the same check as the pre-commit hook:

```bash
scripts/secret-scan
```

Run a full audit before pushing or publishing docs:

```bash
scripts/secret-scan --all
uv run pre-commit run --all-files
```

`--all` is not the pre-commit default: unrelated work may legitimately be present while a small,
independent fix is being committed.

Rules:

- Keep real hosts, IPs, passwords, API keys and Basic Auth values only in gitignored files:
  `.env`, `.env.*`, `deploy/hostkey/.env.secrets` or SSH config.
- Tracked docs may use placeholders such as `<public-url>`, `<client-secret>` and
  `example.invalid`.
- Do not store production URLs with embedded public IPs in generated reports.
- If a real secret ever lands in git history, rotate it first, then clean history with BFG or an
  equivalent tool.

The scanner reports public IPv4/`sslip.io` deployment endpoints, private-key markers, Bearer and
Basic Authorization values, credentials embedded in HTTP URLs, API-key-like tokens and
non-placeholder secret/proxy assignments. It permits loopback addresses, `example.invalid` and
explicit placeholders.

The scanner intentionally does **not** read gitignored runtime data or local credential files.
The hook reads staged blobs from Git's index (therefore a force-staged `.env` is still blocked);
`--all` reads tracked and non-ignored untracked worktree files, while skipping untracked `.env*`,
private keys and certificate bundles. It does not rewrite Git history: that is an incident-response
operation after rotation, not a normal documentation change.
