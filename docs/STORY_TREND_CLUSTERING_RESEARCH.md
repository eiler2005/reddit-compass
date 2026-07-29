# Story/Trend clustering: research notes and implementation roadmap

Этот документ — рабочий контракт для разработчиков и будущих LLM-агентов. Цель:
превратить Radar из ranked list в настоящий trendwatching: `Document → Story → Trend → MetaTrend`.

## Почему это не обычный text clustering

News/story clustering отличается от topic modeling.

- Text clustering группирует похожие тексты.
- Topic modeling ищет общие темы.
- Story identification группирует материалы, которые описывают один конкретный сюжет/событие.

В статье **Real-time News Story Identification** story identification определяется как задача
привязки каждой статьи к конкретной story; авторы подчёркивают, что группировка должна учитывать
particular events, places and people, а не только общую похожесть текста.

Источник: [Real-time News Story Identification](https://arxiv.org/html/2508.08272v1).

Практический вывод для reddit-compass:

```text
Похожие слова ≠ один сюжет.
Один сюжет может иметь разные слова, если источники фреймят его по-разному.
```

Пример:

```text
Same theme, different stories:
- "Spain wins football final in Madrid"
- "France wins football final in Berlin"

Same story, different framing:
- "OpenAI investigates Hugging Face breach"
- "AI executives demand answers after model-hosting incident"
```

## Story vs Trend vs Theme

Нельзя решать всё одной сущностью `Story`.

```text
Document = конкретный материал / пост / статья / продукт.
Story = конкретное событие или один устойчивый сюжет.
Trend = повторяющийся паттерн через несколько stories.
Theme = стабильная taxonomy / рубрика.
MetaTrend = недельный/месячный сдвиг нарратива.
```

Пример:

```text
Story:
Claude shared chats were indexed by Google.

Story:
Hugging Face breach triggers model security questions.

Story:
OpenAI agents leave operational traces.

Trend:
AI tooling becomes an operational security surface.

MetaTrend:
AI safety discussion shifts from model behavior to infrastructure exposure.
```

## Важный вывод про compression ratio

Высокий `compression_ratio` не всегда баг.

В news datasets часто много singleton stories. В Real-time News Story Identification authors
показывают skewed story-size distribution: большинство stories может состоять из одного материала;
поэтому важны outlier detection и баланс между small/large stories.

Практический вывод:

```text
Bad metric:
"Нужно сжать 1000 items в 100 stories".

Better metrics:
- story precision
- overmerge rate
- undermerge rate
- cross-source story recall
- trend usefulness
- evidence coverage
- singleton false-negative audit
```

## Representation stack

Для качественного clustering нужен не один similarity score, а несколько представлений.

### 1. Exact/canonical layer

Используется первым и почти без LLM:

```text
canonical_url
target_url
discussion_url
normalized URL
syndication/canonical redirects
```

Назначение: cheap exact dedupe.

### 2. Sparse lexical layer

```text
normalized title tokens
BM25 / token overlap
headline n-grams
publisher suffix removal
low-signal guards
```

Назначение: быстрый candidate generation, а не финальное решение.

### 3. Entity-aware layer

Entity-aware подход важен, потому что события часто определяются людьми, организациями, местами,
странами, компаниями, продуктами и числами.

В EACL paper **Event-Driven News Stream Clustering using Entity-Aware Contextual Embeddings**
авторы подчёркивают, что entities центральны для event clustering и добавляют entity-awareness
к BERT через внешнюю NER-систему и entity presence embeddings.

Источник: [Event-Driven News Stream Clustering using Entity-Aware Contextual Embeddings](https://aclanthology.org/2021.eacl-main.198.pdf).

Для reddit-compass staged path:

```text
v1: regex/title entity-like extraction
v2: spaCy/Stanza/GLiNER NER offline
v3: entity linking/canonicalization
v4: entity-aware reranker
```

### 4. Dense semantic layer

Dense embeddings нужны для recall: они находят материалы, где wording разный, но смысл близкий.

В Real-time News Story Identification authors используют разные text representation methods,
включая embeddings, explicit NER, summaries и combinations of representations.

Для reddit-compass:

```text
embedding_text =
  title
  + summary_ru/abstract/excerpt
  + extracted entities
  + source_section
```

Первый practical вариант:

```text
vector index в отдельной lab таблице
candidate retrieval top_k=20 per item
no production mutation
```

### 5. Time-aware layer

Story assignment должен учитывать дату публикации/наблюдения.

```text
same story window: 1–14 days depending on domain
trend window: 7–30 days
meta-trend window: 30–90 days
resurfacing: old cluster gets new independent evidence
```

## Recommended algorithm for reddit-compass

### Stage A — immutable release

```bash
reddit-compass lab release create --date YYYY-MM-DD --profile broad
```

No production mutation.

### Stage B — candidate generation

Generate candidate pairs/groups from:

```text
URL exact match
title similarity
token overlap
entity overlap
candidate_themes/pain_points
time window
source diversity
later: dense embeddings
```

Output:

```text
cluster_candidate_pairs
cluster_candidate_groups
```

### Stage C — story proposals

Story proposal means:

```text
These items are likely the same specific event/story.
```

Reject/penalize:

```text
generic listing pages
methodology/podcast/newsletter pages
same broad theme but different event
same sport/political frame but different location/team/person
```

### Stage D — trend proposals

Trend proposal means:

```text
These stories/items indicate the same recurring pattern.
```

Trend proposals can use:

```text
candidate_themes
pain_points
entity buckets
title topic buckets
dense semantic neighborhoods
LLM summarization/reranking
```

### Stage E — LLM adjudication

LLM should not receive the whole corpus.

Correct prompt shape:

```text
Input:
- 3–12 candidate items/stories
- URLs/titles/excerpts/content_scope
- detected entities/themes/pain_points
- current algorithm reason

Task:
- decide same_story / same_trend / reject
- provide JSON only
- cite evidence_item_ids
- explain reject reason
```

Required schema:

```json
{
  "decision": "same_story | same_trend | reject",
  "name": "...",
  "confidence": 0.0,
  "evidence_item_ids": ["..."],
  "reason": "...",
  "risks": ["overmerge | low_signal | insufficient_evidence"]
}
```

Invalid result rules:

```text
unknown evidence_item_ids → reject/retry
no evidence → reject
confidence outside 0..1 → reject
headline-only overclaim → reject
```

### Stage F — eval and promote

No promote before eval/manual review.

Metrics:

```text
pair precision
pair recall
cluster purity
overmerge rate
undermerge rate
cross-source story recall
trend usefulness
evidence coverage
manual accept rate
```

Promotion must create rollback point.

## Current implementation status

Implemented:

```text
trend_engine.db with full frozen Data Releases
independent FacetRelease / StoryRelease / TrendRelease attempts
bounded URL + sparse + entity + optional dense candidate generation
event conflicts and constrained agglomerative Story clustering
stable Story IDs and merge/split provenance
strict cached Qwen adjudication for grey-zone pairs and accepted trend candidates
story-only pattern graph for Trend discovery
Golden Set export/import and measurable publication gates
atomic publication pointers and rollback
read-only Engine UI/API and publication-backed Radar/Today
```

Still pending before production promotion:

```text
install and profile optional E5/spaCy dependencies on the target host
label the first 120 pairs and 30 story groups
pass 50/100/300-item real-data gates
run one full immutable release locally
seven daily shadow publications
manual Broad production publication
```

## Next implementation tasks

1. Review and label the generated Golden Set; do not tune against compression alone.
2. Run the same immutable release at 50, 100 single-domain and 300 mixed-domain items.
3. Compare Story attempts by precision, recall, overmerge and cross-source recall.
4. Review accepted trends for pattern coherence and counterexamples.
5. Profile E5 top-K above the current corpus size; add ANN only above 20,000 items.
6. Start the seven-day shadow rollout after all local gates pass.

The executable contract and exact commands live in
[`TREND_ENGINE.md`](TREND_ENGINE.md).
