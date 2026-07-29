# Reddit Pulse + Verified Story/Trend Radar — Implementation Report

Date: 2026-07-29
Task: `docs/QWEN_REDDIT_PULSE_AND_VERIFIED_TRENDS_TASK.md`
Baseline: `e1f1009` (semantic_dedup_threshold 0.92)
HEAD: `6b3a5b2`

## Commits

| # | Hash | Description |
|---|------|-------------|
| 1 | `0a0ceb2` | Reddit Pulse layer — CommunitySignal schema, scoring, CLI |
| 2 | `3066862` | Verified Story layer + hard guards + safe E5 mode |
| 3 | `aaadfd1` | Trend Watch verified-only + perspective gap |
| 4 | `6b3a5b2` | CLI engine stories verified + broad theme/news_link tests |

## Files Changed

| File | Lines | Purpose |
|------|------:|---------|
| `src/reddit_compass/intelligence/reddit_pulse.py` | 302 | Reddit-native scoring, signal classification, CommunitySignal |
| `src/reddit_compass/intelligence/verified_stories.py` | 314 | Provenance-based verification, group size guards |
| `src/reddit_compass/intelligence/perspective_gap.py` | 114 | Mainstream gap + elite media gap computation |
| `src/reddit_compass/intelligence/engine.py` | +108 | Schema v4, hard guards, safe E5, verified-only trends |
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

## What Was NOT Done (Requires Live Data)

| Item | Reason |
|------|--------|
| Reddit Pulse A/B on prod data | Needs `engine reddit-pulse propose` on VPS with real release |
| Verified story count on prod | Needs `engine stories verified` on VPS |
| Trend propose verified-only on prod | Needs 7+ daily releases for lifecycle |
| Full grey-zone Qwen review | Incremental 200/night strategy in progress |
| UI integration (Radar page) | Separate frontend task |
| API endpoints for reddit-pulse | Separate API task |

## Decision

| Question | Answer |
|----------|--------|
| Ready for shadow? | **Yes** — all code gates pass, schema migrated, CLI functional |
| Ready for production? | **No** — needs live A/B on prod data, 7+ daily releases, Qwen review coverage |

## Next Steps

1. Deploy to VPS and run `engine reddit-pulse propose` on latest release
2. Run `engine stories verified` to measure verified story count
3. Run `engine trends propose --verified-only` to measure trend quality
4. Continue incremental Qwen review (200 pairs/night)
5. Accumulate 7+ daily releases for trend lifecycle
6. Add Radar UI section for Reddit Pulse
7. Add API endpoints `/api/v2/reddit-pulse`
