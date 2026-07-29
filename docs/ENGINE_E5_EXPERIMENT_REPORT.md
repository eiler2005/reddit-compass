# Story/Trend Engine: E5 Embeddings A/B Experiment Report

Date: 2026-07-29
Frozen release: `2026-07-23_2026-07-29-broad-r1` (4,957 items, 7 days)
Embedding model: `intfloat/multilingual-e5-small` (384-dim, MPS)
Qwen review model: `qwen3.8-max-preview` (token-plan, discount window)

## 1. Summary Table

| Variant | Story Release | Stories | Multi-item | Cross-source | Compression | Review pairs |
|---------|--------------|--------:|-----------:|-------------:|------------:|-------------:|
| lexical baseline | stories_7b366b67... | 4,722 | 204 | 54 | 95.26% | — |
| **E5 combined (full)** | **stories_e6e94939...** | **3,489** | **605** | **224** | **70.39%** | 19,736 |
| E5 combined (300 mixed) | stories_2d53dfdc... | 255 | 35 | 12 | 85.0% | 372 |
| E5 combined (300 ai_tech) | stories_1d356701... | 250 | 33 | 11 | 83.33% | 546 |

### Delta vs lexical baseline (full release)

| Metric | Baseline | E5 combined | Delta |
|--------|--------:|------------:|------:|
| Stories | 4,722 | 3,489 | **-1,233 (-26%)** |
| Multi-item | 204 | 605 | **+401 (+197%)** |
| Cross-source | 54 | 224 | **+170 (+315%)** |
| Compression | 95.26% | 70.39% | **-24.87pp** |

## 2. Qwen Story Review (100 grey-zone pairs)

| Decision | Count | % of valid |
|----------|------:|-----------:|
| same_story | 57 | 58% |
| different_story | 37 | 38% |
| uncertain | 4 | 4% |
| invalid | 5 | — |

**Precision of E5 semantic dedup: 57/(57+37) = 60.6%**

Below 90% target. Qwen review corrects grey-zone on re-propose but only 100/19,736 pairs reviewed (0.5%).

## 3. Qwen Trend Review (19 trends)

| Decision | Count |
|----------|------:|
| coherent_trend | 4 |
| reject | 8 |
| invalid | 7 |

**Confirmed trends: 0** (insufficient_history — need 7+ daily releases for lifecycle).

## 4. Top 10 Cross-Source Stories (E5 combined)

| # | Sources | Items | Title |
|---|--------:|------:|-------|
| 1 | 7 | 25 | OpenAI models' Hugging Face breach is a red flag for bankers |
| 2 | 7 | 24 | AI agent went rogue and hacked startup by itself, OpenAI reveals |
| 3 | 5 | 10 | Trump vows to investigate EU over fining of US tech companies |
| 4 | 5 | 8 | Japan earthquake injures at least 100 and kills two inside shopping mall |
| 5 | 5 | 6 | Oil prices hit $100 for the first time since May |
| 6 | 5 | 6 | Open or Shut? The A.I. Debate That's Driving a Wedge Through Big Tech |
| 7 | 4 | 10 | Cisco Antares: A New Family of Cheap, Open-Source, Compact Security AI Models |
| 8 | 4 | 10 | Oil Prices Sink After U.S. and Iran Pause Fighting a Second Day |
| 9 | 4 | 8 | Nvidia Forms Alliance to Back Open-Source A.I. Amid Debate Over Safety |
| 10 | 4 | 7 | Trump administration bans new Chinese humanoid robots |

### Top story breakdown (7 sources, 25 items)

"OpenAI models' Hugging Face breach" merged items from:
- American Banker (story medoid)
- BBC (semantic embedding dedup)
- Hacker News × 7 (semantic embedding dedup)
- Reddit (semantic embedding dedup)
- Reuters (semantic embedding dedup)
- Washington Post (semantic embedding dedup)

## 5. Good Cross-Source Merges (5 examples)

1. **OpenAI HuggingFace breach** — 7 sources, 25 items. Same security incident covered by American Banker, BBC, HN, Reddit, Reuters, WaPo. ✓
2. **AI agent rogue hack** — 7 sources, 24 items. Same event, multiple angles. ✓
3. **Japan earthquake** — 5 sources, 8 items. Natural disaster, cross-source confirmation. ✓
4. **Oil prices $100** — 5 sources, 6 items. Market event, multiple outlets. ✓
5. **Trump/EU tech fines** — 5 sources, 10 items. Political event, cross-source. ✓

## 6. Suspicious / False-Positive Merges (5 examples)

1. **"Open Source Will Eat AI"** merged with "OpenAI Woos Trump Administration as Investor" — different stories (open source AI vs OpenAI politics). OVERMERGE.
2. **"Why does everything feel so joyless?"** merged with Medium article "Comment Your Social Media Handle Below..." — unrelated articles with semantic similarity. OVERMERGE.
3. **"Trump tariffs"** — BBC "UK's Trump trade deal" + Guardian "Trump's new tariffs on 80 countries" — related but different angles. BORDERLINE.
4. **"OpenAI debate wedge through Big Tech"** — merged 5 sources but some items are about different aspects of the AI debate. BORDERLINE.
5. **"Cisco Antares open-source AI"** — merged 4 sources but some items are about different open-source AI initiatives. BORDERLINE.

## 7. Recommendations

### Keep
- **E5 combined** as default story engine variant. The +315% cross-source improvement is too significant to ignore.
- **lexical-hash-v1** as fallback when sentence-transformers unavailable.
- **near-duplicate pass** (minhash/simhash) — adds small bonus on top of semantic.

### Switch
- **Default embedding model**: `intfloat/multilingual-e5-small` (was lexical-hash-v1).
- **Default story method**: `hybrid_v2` with `semantic_dedup_enabled=true`.

### Require
- **Qwen review before any publish**: 60% precision on auto-merges is not production-ready.
- **7+ daily releases** before confirming trends (lifecycle requirement).
- **Full grey-zone review** (19,736 pairs) before publishing story release.

### Reject
- **semantic-dedup with lexical-hash-v1**: almost no benefit over baseline.
- **Publishing without Qwen review**: overmerge rate too high.

### Next steps
1. Run full Qwen review on all 19,736 grey-zone pairs (estimated 3-4 hours with qwen3.8-max-preview).
2. Re-propose story release with all cached decisions.
3. Accumulate 7+ daily releases for trend lifecycle.
4. Re-evaluate trend confirmation after 7 releases.
5. Consider raising `semantic_dedup_threshold` from 0.88 to 0.92 to reduce overmerges.

## 8. Artifacts

| Artifact | ID | Location |
|----------|-----|----------|
| Data release | `2026-07-23_2026-07-29-broad-r1` | `prod_trend_engine_eval.db` |
| Facet release | `facets_3f101ad5bd24e30803db` | same DB |
| Story release (E5+Qwen) | `stories_e6e94939c91354ab2333` | same DB |
| Trend release | `trends_49842404428a6890db15` | same DB |
| E5 embeddings | 4,869 vectors cached | same DB |
| Qwen story reviews | 103 (98 valid) | same DB, `llm_reviews` |
| Qwen trend reviews | 19 (12 valid) | same DB, `llm_reviews` |
