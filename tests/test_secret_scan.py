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
