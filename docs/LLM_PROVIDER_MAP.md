# LLM Provider Map and Migration Rule

## Purpose

This is the concise operational inventory of every LLM boundary in
reddit-compass. It complements QWEN_ROUTING.md, which is the detailed source
of truth for Qwen endpoint selection, grant handling, usage ledger, and
current pricing evidence.

The live code uses Qwen directly. DeepSeek is **not** a configured runtime
fallback in this project. A future provider must be introduced as a real,
tested adapter and route; adding a key name alone is not a migration.

Never put a real API key, provider response containing sensitive material, or
tokenised endpoint in this map. Use only variable names and sanitized examples.

## Project rule for any LLM change

1. Update this map, QWEN_ROUTING.md or its replacement, sanitized secret
   templates, tests, README, and CHANGELOG in the same change.
2. Record each workload, provider endpoint family, exact model identifier,
   prompt/cache impact, retry behavior, cost guard, and rollback route.
3. Preserve the deterministic pipeline: collection, URL/entity handling,
   clustering, temporal gates, embeddings, and quality gates must remain
   usable without an LLM.
4. Verify current model availability, prices, quotas, regional endpoint, and
   terms with the provider on the day of an operational change. Pricing is
   external mutable data and belongs in dated evidence, not hard-coded here.
5. Run the migration in shadow or on a limited release first. Publish only
   after quality gates pass and an operator approves the release.

## LLM route inventory

| Workload | Owner | Normal model route | Deterministic safety boundary |
|---|---|---|---|
| Post/signal analysis and bounded structured extraction | src/reddit_compass/signals.py | Qwen qwen3.7-flash through the Pay-As-You-Go compatible endpoint | A failed model call does not turn collected source data into a fabricated signal |
| Engine schema extraction | src/reddit_compass/intelligence/trend_schema_llm.py and cli.py | Qwen qwen3.7-flash bulk route | Story/trend graph construction, temporal logic, and quality gates remain deterministic |
| Actor normalization | cli.py plus Engine schema helpers | Qwen qwen3.7-flash bulk route | Canonical storage/validation remains controlled by project schemas |
| Pulse classification | signals.py and Engine orchestration | Qwen qwen3.7-flash bulk route | Classifications are bounded by validated schemas and do not publish by themselves |
| Story-pair and trend review | intelligence/engine.py and cli.py | Qwen qwen3.7-flash bounded JSON review | Review is cached and quality-gated; publication is not automatic |
| Manual complex synthesis | signals.py synthesis path | Qwen qwen3.8-max only after explicit escalation | It is not the normal bulk/review route and does not bypass the release gate |

Normal collection is deliberately LLM-free. The pipeline has no general
DeepSeek fallback; error/retry behavior is defined at the Qwen call boundary
and the release remains unpublished when required quality work is incomplete.

## Credentials and configuration

| Variable | Role | Rule |
|---|---|---|
| DASHSCOPE_API_KEY | Primary Engine key for Qwen Pay-As-You-Go compatible API | Preferred service credential; keep only in the ignored secret environment. |
| QWEN_PAY_AS_YOU_GO_PLAN_KEY | Accepted compatibility alias for the same Pay-As-You-Go route | Use only when the deployment cannot yet rename the existing secret; do not set conflicting values. |
| QWEN_Pay_As_You_Go_PLAN_KEY | Legacy-casing compatibility alias | Compatibility only; new deployments should prefer DASHSCOPE_API_KEY. |
| QWEN_TOKEN_PLAN_KEY | Interactive Token Plan credential | The Engine does not use this endpoint for normal service work. It must not be silently substituted for the Pay-As-You-Go key. |
| RC_QWEN_LEDGER_PATH | Local usage-ledger location | Defaults to qwen_usage.db under the data directory. Preserve it in backup/restore operations. |
| RC_QWEN_PAYG_FREE_TOKENS | Explicitly confirmed grant/quota | Zero means no assumed grant. Never invent a provider quota in configuration. |
| RC_QWEN_PAYG_GRANT_START and RC_QWEN_PAYG_GRANT_PER_MODEL | Grant lifetime/scope | Used for transparent routing/accounting only after an owner verifies the grant. |
| RC_QWEN_MAX_SPEND_CNY and RC_QWEN_SPEND_WINDOW_DAYS | Local spend guard | A configured limit is checked before a call; empty limit means no local cap. |

The endpoint ownership and key compatibility details are maintained in
QWEN_ROUTING.md. Secret templates document names only. A populated .env file
or host secret file is never a tracked artifact.

## Cost, reproducibility, and observation

- qwen_policy.py records request usage by model, endpoint, tokens, stage, and
  time in qwen_usage.db. Use the project Qwen CLI report before/after a bulk
  run and retain the ledger in backups.
- The code distinguishes bulk work from rare synthesis. Do not move a
  high-volume task to a high-cost model without a measured quality reason and
  an explicit updated spend cap.
- Review-model identity is part of the llm_reviews cache key. Changing a review
  model requires treating prior cached reviews as a different model result,
  rather than pretending they are interchangeable.
- Schema extraction also has cache/versioning contracts. A provider/model
  migration must document whether cached output is kept, revalidated, or
  regenerated.
- The local spend guard complements, but does not replace, provider-side
  budgets and alerts. Check both after any large backfill or nightly-route
  change.

## Safe provider/model migration

1. Inventory direct Qwen callers, environment variables, CLI commands,
   scheduled jobs, cache tables, backups, and documentation.
2. Add a provider adapter with the same explicit request/response validation;
   do not hide it behind a Qwen variable or assume OpenAI-compatible means
   behavior-compatible.
3. Choose a model per workload. Keep bulk extraction/classification, bounded
   JSON review, and rare synthesis as separate cost/quality tiers.
4. Decide the cache policy before running: model identifiers participate in
   reproducibility. Do not mix old and new review outputs under one identity.
5. Configure a conservative local spend limit and provider-side budget alert,
   then run fixtures and a limited shadow/preview release. Inspect ledger
   stages, malformed-response retries, quality report, and cost.
6. Publish only when the existing quality and manual approval gates pass. If
   the result regresses, keep the prior published pointer and roll back the
   route/configuration.
7. Record model, endpoint type, key variable name, change date, cache decision,
   measured quality/cost, and rollback evidence in this map and CHANGELOG.

## Change checklist

- Which Engine or signal stage invokes the model?
- Which model tier and endpoint serve it, and what key variable is read?
- Does the change preserve structured response validation and retries?
- What happens to llm_reviews, story_schemas, and the usage ledger?
- Is the local spend cap configured and is the provider dashboard alert active?
- Has a shadow/preview release passed the quality gates?
- Can the current published release remain untouched while the route is rolled
  back?
