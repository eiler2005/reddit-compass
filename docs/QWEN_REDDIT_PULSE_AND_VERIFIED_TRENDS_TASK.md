# Qwen task: Reddit Pulse + verified Story/Trend Radar

Date: 2026-07-29
Repo: `reddit-compass`
Current baseline commit: `2dda4d2 docs: E5 embeddings A/B experiment report`

## 0. Goal

Stop optimizing for maximum automatic clustering.

Build a practical editorial radar with two measurable targets:

```text
20-50 reliable cross-source stories per day
5-10 useful reviewed trends per week
```

This task has two tracks:

1. **Reddit Pulse** — separate Reddit-native trendwatching for AI and broad social/cultural signals.
2. **Verified Story/Trend layer** — Radar uses only high-confidence stories and reviewed trends.

Do not publish to production `broad` until gates pass.

## 1. Current facts

### Lexical/near baseline

Frozen release: `2026-07-23_2026-07-29-broad-r1`, 4,957 items.

```text
combined_near_and_semantic with lexical-hash-v1:
  4,722 stories
  204 multi-item stories
  54 cross-source stories
  95.26% compression
  6 pending trends
  0 confirmed trends
```

### E5 aggressive result

```text
E5 combined:
  3,489 stories
  605 multi-item stories
  224 cross-source stories
  70.39% compression
  19 pending trends
  0 confirmed trends
```

Problem: E5 aggressive has major overmerge risk.

Observed false-positive clusters:

- many unrelated `Show HN` projects merged into one pseudo-story;
- broad AI/open-source/agent topics became fake stories;
- `Hugging Face Has a Deepfake Nudes Problem` merged into OpenAI/HuggingFace breach.

Therefore:

- E5 is useful for retrieval;
- E5 semantic auto-merge is not safe as default;
- Qwen/manual review gates are mandatory.

## 2. Product principle

Split the system into three layers:

```text
News Inbox
  raw collected materials, weak clustering allowed

Story Radar
  verified/high-confidence concrete stories only

Trend Watch
  recurring patterns built only from verified stories and Reddit Pulse signals
```

Radar must prefer fewer reliable objects over many noisy clusters.

## 3. Reddit Pulse

Reddit must not be treated as just another news provider.

It should answer:

```text
What are people discussing?
Where is pain, fear, desire, backlash, meme/culture shift?
What is gaining velocity before mainstream sources notice?
What matters for AI/book/RBC/business?
```

### 3.1 Expand Reddit AI and broad packs

Check `config/profiles/broad.json` and current Reddit source config.

Add or verify packs for:

#### AI / technology

- `r/ChatGPT`
- `r/OpenAI`
- `r/ClaudeAI`
- `r/LocalLLaMA`
- `r/singularity`
- `r/artificial`
- `r/MachineLearning`
- `r/StableDiffusion`
- `r/AI_Agents`
- `r/AutoGPT`
- `r/LangChain`
- `r/ollama`
- `r/LocalLLM`
- `r/technology`
- `r/Futurology`

Use only public, read-only Reddit access.

#### Labor / career

- `r/cscareerquestions`
- `r/careerguidance`
- `r/jobs`
- `r/recruitinghell`
- `r/antiwork`
- `r/overemployed`
- `r/Teachers`
- `r/Professors`

#### Business / finance / startups

- `r/startups`
- `r/Entrepreneur`
- `r/smallbusiness`
- `r/investing`
- `r/stocks`
- `r/wallstreetbets`
- `r/economy`
- `r/finance`

#### Culture / society / politics

- `r/news`
- `r/worldnews`
- `r/politics`
- `r/geopolitics`
- `r/privacy`
- `r/technology`
- `r/socialmedia`
- `r/OutOfTheLoop`

#### Sports

- `r/sports`
- `r/nba`
- `r/nfl`
- `r/soccer`
- `r/formula1`
- `r/tennis`

Do not overcollect blindly. Keep per-pack limits config-driven.

### 3.2 Add `CommunitySignal`

Add a separate read model in `trend_engine.db` or an Engine table:

```text
community_signals
  signal_release_id
  signal_id
  provider              -- reddit
  item_id
  subreddit
  pack_id
  signal_type
  title
  discussion_url
  target_url
  pulse_score
  subreddit_percentile
  score_velocity
  comment_velocity
  discussion_depth
  comment_score_ratio
  cross_subreddit_repetition
  novelty
  domain_ids
  theme_ids
  pain_points
  project_scores
  linked_story_id nullable
  mainstream_coverage_count
  perspective_gap
  created_at
```

Do not mix `pulse_score` with news story score.

### 3.3 Reddit-native scoring

Implement:

```text
reddit_pulse_score =
  0.30 * subreddit_percentile
  0.25 * comment_velocity
  0.20 * discussion_depth
  0.15 * cross_subreddit_repetition
  0.10 * novelty
```

Rules:

- raw score is never compared across subreddits directly;
- compute percentile within subreddit and date/window;
- age-adjust velocity;
- score comments separately from upvotes;
- repeated topic across subreddits increases signal, but exact reposts do not create fake diversity.

Suggested metrics:

```text
subreddit_percentile = percentile(score within subreddit/day)
comment_velocity = comments per hour, percentile within subreddit/day
discussion_depth = log1p(comments) * upvote_ratio guard
comment_score_ratio = comments / max(score, 1)
cross_subreddit_repetition = normalized count of similar high-confidence Reddit stories
novelty = 1 - seen_similar_in_last_7d
```

### 3.4 Reddit signal types

Classify each Reddit item into:

```text
news_link
discussion
question
pain_point
complaint
meme_culture
product_request
career_labor
market_investing
policy_politics
ai_capability
ai_risk
ai_tools
other
```

Use deterministic rules first. Qwen can enrich only top items.

Example deterministic hints:

- title has `how do I`, `anyone else`, `what are you using` → `question`;
- title has `hate`, `broken`, `can't`, `burnout`, `laid off` → `pain_point` / `complaint`;
- target URL exists and is not reddit → `news_link`;
- `r/wallstreetbets`, `r/stocks`, `r/investing` → `market_investing`;
- `r/recruitinghell`, `r/jobs`, `r/cscareerquestions` → `career_labor`.

### 3.5 Reddit Pulse UI/API

Add read-only endpoints:

```text
GET /api/v2/reddit-pulse?date=YYYY-MM-DD&domain=&pack=&signal_type=
GET /api/v2/reddit-pulse/{signal_id}
```

Add UI section in Radar:

```text
Reddit Pulse
  Hot discussions
  Pain points
  AI/tooling pulse
  Career/labor anxiety
  Market/investing pulse
  Culture/meme shifts
  Mainstream gap
```

Do not hide Reddit Pulse behind generic Stories.

## 4. Verified Story layer

### 4.1 Define verified stories

Create a `verified_story` filter/read model.

A Story is verified if at least one condition holds:

```text
exact_event_url_match
near_duplicate_title_fingerprint
shared canonical/target URL from independent providers
Qwen-confirmed same_story
manual label same_story
single-source but high Reddit Pulse and explicitly marked community_only
```

E5 semantic similarity alone is not enough.

### 4.2 E5 retrieval mode

Add a safer E5 mode:

```text
e5_retrieval_review_only
```

Behavior:

- E5 generates candidate pairs;
- semantic pairs default to `review`;
- semantic auto-merge only if very strict:

```text
dense_similarity >= 0.94
AND date_distance <= 3
AND no hard conflicts
AND shared non-generic named entity
AND (title_score >= 0.75 OR token_jaccard >= 0.45 OR exact target_url)
AND NOT generic_show_hn_project_cluster
```

### 4.3 Hard guards

Implement guards:

- `Show HN` items must not auto-merge with other `Show HN` items by semantic similarity alone.
- Same-provider HN/HN semantic clusters require exact URL or near-duplicate title.
- Generic anchors do not count as entity anchors:

```text
ai
agent
agents
open source
startup
tool
model
llm
company
app
platform
```

- Broad topics cannot become stories.
- Story group max size guard:

```text
same-provider-only story with > 8 items requires exact URL/near-duplicate provenance
cross-source story with > 15 items requires Qwen group review or manual label
```

### 4.4 Group review

Add Qwen group review for large/ambiguous groups.

Prompt must allow partition, not only accept/reject:

```json
{
  "decision": "same_story | split | reject | uncertain",
  "groups": [
    {
      "story_name": "",
      "item_ids": [],
      "event_frame": {
        "actors": [],
        "action": "",
        "object": "",
        "geography": [],
        "event_date": ""
      },
      "evidence_item_ids": [],
      "confidence": 0.0
    }
  ],
  "conflicts": [],
  "reason": ""
}
```

Invalid if:

- unknown item IDs;
- no evidence IDs;
- confidence outside 0..1;
- group count does not explain all input IDs for `split`.

## 5. Trend Watch

Trends must be built only from:

```text
verified cross-source stories
Qwen-confirmed stories
high-confidence Reddit Pulse signals
project-relevant saved stories
```

Do not build trends over all raw singleton stories.

### 5.1 Weekly trend target

Target:

```text
5-10 useful reviewed trends per week
```

Trend candidate requirements:

```text
>= 3 verified stories/signals
>= 2 different dates
>= 2 independent source clusters OR marked community_only with high Reddit Pulse
clear recurring pattern
counterpoints checked
Qwen trend review valid
```

### 5.2 Trend types

Add:

```text
cross_source_confirmed
reddit_first
mainstream_only
community_only
perspective_gap
project_relevant
```

### 5.3 Perspective gap

Compute:

```text
mainstream_gap =
  high Reddit Pulse + low mainstream coverage

elite_media_gap =
  high mainstream coverage + low Reddit discussion
```

Show in Radar:

```text
What people discuss but media misses
What media pushes but communities ignore
```

## 6. CLI tasks

Add commands:

```bash
reddit-compass engine reddit-pulse propose \
  --release RELEASE_ID \
  --date YYYY-MM-DD \
  --profile broad

reddit-compass engine reddit-pulse inspect \
  --signal-release SIGNAL_RELEASE_ID \
  --limit 50

reddit-compass engine stories verified \
  --story-release STORY_RELEASE_ID \
  --signal-release SIGNAL_RELEASE_ID

reddit-compass engine trends propose \
  --story-release STORY_RELEASE_ID \
  --signal-release SIGNAL_RELEASE_ID \
  --verified-only \
  --window 7d
```

If schema work is too large, implement first as JSON export:

```text
data/engine_exports/reddit-pulse-YYYY-MM-DD.json
data/engine_exports/verified-stories-YYYY-MM-DD.json
```

But prefer SQLite versioned releases if feasible.

## 7. A/B experiments to run

Use current copied prod snapshot if available:

```text
data/prod_trend_engine_eval.db
release: 2026-07-23_2026-07-29-broad-r1
facet: facets_3f101ad5bd24e30803db
```

### 7.1 Reddit Pulse

Run for the latest available date.

Expected output:

```text
total reddit items
items by pack
top 20 pulse signals
top 10 AI/tooling signals
top 10 pain points
top 10 mainstream_gap candidates
```

### 7.2 Verified stories

Compare:

```text
all stories
verified stories only
verified cross-source stories
community_only Reddit Pulse stories
```

Target daily:

```text
20-50 reliable cross-source stories
```

For 7-day release, rough target:

```text
140-350 reliable cross-source stories
```

But quality matters more than count.

### 7.3 Trends

Run trend propose on verified-only input.

Expected:

```text
candidate trends <= 30
reviewed trends 5-10/week
confirmed trends > 0
obvious broad-theme trends = 0
```

## 8. Tests

Add tests for:

- Reddit percentile is computed within subreddit, not globally.
- Viral `r/news` raw score does not suppress small-subreddit high-percentile signal.
- `Show HN` semantic-only items do not auto-merge.
- E5 retrieval can create review candidates without auto-merge.
- Verified stories exclude semantic-only auto merges unless Qwen/manual confirmed.
- Reddit news_link with target_url links to existing article story.
- Mainstream gap computed for high Reddit Pulse + low mainstream coverage.
- Trend Watch uses verified-only stories by default.
- Broad themes such as `ai`, `open source`, `startup` cannot become trends.
- Qwen group review can split an overmerged group.
- API/UI endpoints escape titles and validate URLs.

## 9. Quality gates

Shadow gate:

```text
verified cross-source stories/day: 20-50
sample precision on 30 verified stories: >= 90%
Reddit Pulse top 30 sample usefulness: >= 80%
confirmed reviewed trends/week: 5-10
obvious overmerge in top-20 largest verified stories: 0
```

Production gate:

```text
pair precision >= 95%
pair recall >= 75%
overmerge <= 3%
cross-source recall >= 75%
trend usefulness >= 80%
7 daily finalized releases
manual publish only
```

## 10. Required final report

Return:

```text
commit hash
files changed
commands run
test results
Reddit Pulse metrics
verified Story metrics
Trend metrics
sample good stories
sample rejected/overmerge stories
decision: ready for shadow? yes/no
decision: ready for production? must be no unless gates are proven
```

Use tables:

| layer | metric | value | target | decision |
|---|---:|---:|---:|---|

| sample | title | reason | decision |
|---|---|---|---|

Do not deploy or publish unless explicitly asked after review.
