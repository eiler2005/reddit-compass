"""Contracts for the project-specific staged secret scanner."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType


def _scanner_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "secret-scan"
    loader = SourceFileLoader("reddit_compass_secret_scan", str(path))
    spec = importlib.util.spec_from_loader("reddit_compass_secret_scan", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plain_python_tokens_are_not_secret_assignments() -> None:
    scanner = _scanner_module()

    findings = scanner.scan_text("src/example.py", "tokens = title.split()\n")

    assert findings == []


def test_non_placeholder_environment_secret_is_reported() -> None:
    scanner = _scanner_module()

    findings = scanner.scan_text("config/example.env", "DASHSCOPE_API_KEY=live-value\n")

    assert findings == ["config/example.env:1: non-placeholder secret assignment"]


def test_placeholder_environment_secret_is_allowed() -> None:
    scanner = _scanner_module()

    findings = scanner.scan_text(".env.example", "DASHSCOPE_API_KEY=<your-key>\n")

    assert findings == []


def test_public_deployment_endpoint_and_sslip_host_are_reported() -> None:
    scanner = _scanner_module()
    sample_ip = ".".join(("8", "8", "8", "8"))

    findings = scanner.scan_text(
        "docs/operations.md",
        f"Dashboard: https://{sample_ip}.sslip.io/today\n",
    )

    assert findings == ["docs/operations.md:1: sslip host with embedded public IP"]

    direct_ip = scanner.scan_text("docs/operations.md", f"Dashboard: https://{sample_ip}/today\n")
    assert direct_ip == [f"docs/operations.md:1: public IPv4 in tracked content ({sample_ip})"]


def test_basic_auth_and_proxy_assignments_are_reported() -> None:
    scanner = _scanner_module()
    proxy_url = "http://user:" + "password" + "@proxy.invalid"

    findings = scanner.scan_text(
        "deploy/example.env",
        f"RC_BASIC_AUTH=admin:actual-password\nREDDIT_COMPASS_PROXIES={proxy_url}\n",
    )

    assert findings == [
        "deploy/example.env:1: non-placeholder secret assignment",
        "deploy/example.env:2: credential embedded in URL",
        "deploy/example.env:2: non-placeholder secret assignment",
    ]


def test_full_audit_does_not_read_untracked_private_env_files() -> None:
    scanner = _scanner_module()

    assert scanner.skip_private_worktree_path(".env")
    assert scanner.skip_private_worktree_path("deploy/hostkey/.env.secrets")
    assert scanner.skip_private_worktree_path("keys/id_rsa")
    assert not scanner.skip_private_worktree_path(".env.example")
