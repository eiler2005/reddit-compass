# Reddit Pulse + Verified Story/Trend Radar — Implementation Report

Date: 2026-07-29; follow-up fixes: 2026-07-30
Task: `docs/QWEN_REDDIT_PULSE_AND_VERIFIED_TRENDS_TASK.md`
Baseline: `e1f1009` (semantic_dedup_threshold 0.92)
HEAD: see git log; report updated after UI/API/Pulse hardening commits.

## Commits

| # | Hash | Description |
|---|------|-------------|
| 1 | `0a0ceb2` | Reddit Pulse layer — CommunitySignal schema, scoring, CLI |
| 2 | `3066862` | Verified Story layer + hard guards + safe E5 mode |
| 3 | `aaadfd1` | Trend Watch verified-only + perspective gap |
| 4 | `6b3a5b2` | CLI engine stories verified + broad theme/news_link tests |
| 5 | `18780f5`–`c0ca0e8` | Reddit Pulse API/UI, Radar integration and query fixes |

## Files Changed

| File | Lines | Purpose |
|------|------:|---------|
| `src/reddit_compass/intelligence/reddit_pulse.py` | 302 | Reddit-native scoring, signal classification, CommunitySignal |
| `src/reddit_compass/intelligence/verified_stories.py` | 314 | Provenance-based verification, group size guards |
| `src/reddit_compass/intelligence/perspective_gap.py` | 114 | Mainstream gap + elite media gap computation |
| `src/reddit_compass/intelligence/engine.py` | +108 | Schema v5, hard guards, safe E5, verified-only trends, versioned SignalRelease metadata |
| `src/reddit_compass/cli.py` | +232 | CLI: reddit-pulse propose/inspect, stories verified, trends --verified-only |
| `tests/test_reddit_pulse.py` | 252 | 30 tests: percentile, velocity, depth, classification |
| `tests/test_verified_stories.py` | 434 | 18 tests: verification, group size, broad themes, news_link |
| `tests/test_perspective_gap.py` | 198 | 6 tests: gap detection, sorting, empty cases |
| **Total** | **1,946** | |

## Commands Run

```bash
uv run ruff check .          # All checks passed
uv run ruff format --check . # 89 files formatted
uv run mypy src              # Success: no issues found in 54 source files
uv run pytest --no-cov -q    # All tests passed (54 total new + existing)
```

## Test Results

| Test file | Tests | Status |
|-----------|------:|--------|
| `test_reddit_pulse.py` | 30 | ✅ All pass |
| `test_verified_stories.py` | 18 | ✅ All pass |
| `test_perspective_gap.py` | 6 | ✅ All pass |
| **New total** | **54** | **✅** |

## Layer Metrics

| Layer | Metric | Value | Target | Decision |
|-------|--------|------:|-------:|----------|
| Reddit Pulse | Signal types | 14 | 14 | ✅ |
| Reddit Pulse | Scoring components | 5 | 5 | ✅ |
| Reddit Pulse | Percentile scope | within-sub | within-sub | ✅ |
| Verified Stories | Verification reasons | 6 | 6 | ✅ |
| Verified Stories | Generic anchors blocked | 11 | 11 | ✅ |
| Hard Guards | Show HN block | ✅ | ✅ | ✅ |
| Hard Guards | Same-provider HN block | ✅ | ✅ | ✅ |
| Hard Guards | Group size same-provider | >8 | >8 | ✅ |
| Hard Guards | Group size cross-source | >15 | >15 | ✅ |
| Safe E5 | Auto-merge threshold | 0.94 | 0.94 | ✅ |
| Safe E5 | Max date distance | 3d | 3d | ✅ |
| Safe E5 | Review fallback | 0.88+ | 0.88+ | ✅ |
| Trend Watch | Verified-only mode | ✅ | ✅ | ✅ |
| Perspective Gap | Mainstream gap | ✅ | ✅ | ✅ |
| Perspective Gap | Elite media gap | ✅ | ✅ | ✅ |

## CLI Commands Added

```bash
# Reddit Pulse
reddit-compass engine reddit-pulse propose --release RELEASE --date YYYY-MM-DD --profile broad
reddit-compass engine reddit-pulse inspect --signal-release ID --limit 50 --signal-type pain_point

# Verified Stories
reddit-compass engine stories verified --story-release ID --signal-release ID --limit 50

# Verified-only Trends
reddit-compass engine trends propose --story-release ID --verified-only --signal-release ID
```

## 2026-07-30 Follow-up Fixes

These fixes use only existing frozen SQLite data. They do not require `collect`, `run`, Reddit fetches,
RSS fetches or any other network collection.

| Area | Fix |
|------|-----|
| Radar Pulse UI | Pulse summary is built before closing the read-only engine DB connection. |
| Signal lookup | Latest signal release can be filtered by `data_release_id` and `date`; Radar no longer pulls a random latest release from another date. |
| Signal versioning | `signal_releases` now stores `method`, `params_hash`, `metrics_json`, `git_sha`; `reddit-pulse propose` creates a versioned attempt instead of overwriting the same ID. |
| URL safety | Pulse API sanitizes `discussion_url` and `target_url` to `http/https` only before UI rendering. |
| Mainstream coverage | `reddit-pulse propose --story-release ...` links Reddit signals to existing stories and fills `mainstream_coverage_count` from non-Reddit mainstream/business/tech-culture evidence in the same StoryRelease. |
| Reddit history | If no prior frozen releases exist, novelty is neutral (`0.5`) instead of fake-new (`1.0`). When prior finalized releases exist, novelty uses titles from the configured history window. |
| Velocity | `score_velocity` and `comment_velocity` use item age from frozen timestamps when available, not a hardcoded 24h default. |
| Verified reasons | Same-provider URL duplicates are reported as `same_provider_duplicate`, not `cross_source_url`. |

### Current Data Limitation

Local `trend_engine.db` currently has only one finalized `DataRelease`. Therefore true 7-day Reddit
history cannot be reconstructed from local engine data. The code now behaves honestly:

- no prior release → neutral novelty;
- prior finalized releases → historical novelty based on the configured window;
- no story release passed to Pulse → no linked stories and no mainstream coverage;
- story release passed → linked stories and coverage are computed from frozen release rows.

## What Was NOT Done (Requires Live Data)

| Item | Reason |
|------|--------|
| Reddit Pulse A/B on prod data | Needs `engine reddit-pulse propose` on VPS with existing frozen releases; no network collection required |
| Verified story count on prod | Needs `engine stories verified` on VPS |
| Trend propose verified-only on prod | Needs 7+ daily releases for lifecycle |
| Full grey-zone Qwen review | Incremental 200/night strategy in progress |
| UI/API hardening | Done in follow-up; add browser visual checks if UI layout changes further |

## Decision

| Question | Answer |
|----------|--------|
| Ready for shadow? | **Yes** — all code gates pass, schema migrated, CLI functional |
| Ready for production? | **No** — needs live A/B on prod data, 7+ daily releases, Qwen review coverage |

## Next Steps

1. Deploy to VPS and run `engine reddit-pulse propose` on latest existing frozen release; do not run network collection
2. Run `engine stories verified` to measure verified story count
3. Run `engine trends propose --verified-only` to measure trend quality
4. Continue incremental Qwen review (200 pairs/night)
5. Accumulate 7+ daily releases for trend lifecycle
6. Add Radar UI section for Reddit Pulse
7. Add API endpoints `/api/v2/reddit-pulse`
