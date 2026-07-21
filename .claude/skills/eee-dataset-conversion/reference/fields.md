# Aggregate fields — what each holds, what confuses people, the fix

*Scope: aggregate `EvaluationLog` field semantics. Instance/jsonl fields →
`instance-level.md`; deeper failure modes → `gotchas.md`.*

Two collision clusters cause ~80% of mistakes:
- **"where from?" is three fields:** `source_metadata` (who *reported*),
  `source_data` (the *dataset*), the per-score *citation* (no typed home).
- **"what's its name?" is three fields:** `evaluation_name` (the eval),
  `metric_config.metric_name` (the metric), `source_metadata.source_name` (the venue).

## §shape — decide before writing code
1. **Who produced the scores?** you ran it (have raw outputs) → `evaluation_run`;
   you scraped reported numbers → `documentation`. Item-level data is a strong
   tell for `evaluation_run`.
2. **Aggregate-only or item-level?** headline per (model, benchmark) → aggregate
   `.json` (always); per-example too → instance `_samples.jsonl` (see
   `instance-level.md`).
3. **Grain?** default = **one log per model**, all benchmarks in
   `evaluation_results[]`; use **one log per (model, benchmark)** only when a
   benchmark has many subtasks and/or its own per-benchmark instance sidecar.

## source_metadata (per log)
- `source_type` — `documentation` (scraped) vs `evaluation_run` (ran it).
- `source_name` — the **platform/leaderboard**, NOT the benchmark or author.
- `source_organization_name` — the **aggregator/publisher org**, NOT a username
  or the model developer.
- `evaluator_relationship` — relative to the **model developer**, not the reporter.
  A leaderboard running its own eval is still `third_party`. Enum:
  `first_party|third_party|collaborative|other` (no `self`).

## model_info
- `id` — HF `developer/model`; **canonicalize via the registry**, don't invent.
  Don't bake effort/mode/quant tiers into it; dated snapshots are fine.
- `name` = raw/display; `developer` = the org; `inference_platform` (API host) vs
  `inference_engine` (vLLM) — `unknown` acceptable.

## evaluation_results[] + source_data + metric_config
- `evaluation_name` — the **eval**, a namespaced dotted id (`wild.<task>.<subtask>`),
  not a free-text title.
- `evaluation_result_id` — optional stable per-result id; **set it** if you emit
  instance sidecars (the join key each line points at).
- `source_data` — the **dataset the eval ran on** (`hf_dataset`/`url`/`other`),
  NOT the results dataset and NOT the model. Verify the repo exists.
- `metric_config.metric_name` — the **metric** (`accuracy`, `pass rate`), NOT the
  eval. Most-conflated field. `metric_id` — namespaced; `metric_kind` — the
  normalized family. There is **no `metric_type`** field.
- `metric_config.score_type` — `binary|continuous|levels`. Traps: (a) omitting it
  fires the `levels` branch → requires `level_names` **and** `has_unknown_level`;
  (b) `continuous` **requires** `min_score`+`max_score`; (c) there is **no unbounded
  type** (see gotchas.md). (The JSON schema enforces (a); pydantic `validate` does
  not — set `score_type` explicitly regardless.)
- `lower_is_better` — required; the inverse of `higher_is_better`.
- `score_details` — `score` + optional `uncertainty` (`standard_error`,
  `num_samples`) + `details` (strings).

## eval_library (per log)
- The **harness** that ran it (`inspect_ai`, `lm-evaluation-harness`, `helm`), NOT
  the platform/aggregator/benchmark. **Independent of `source_type`** — a
  `documentation` source can still name a *known* harness (lm-eval's
  `acc,none`/`acc_stderr,none`/`bootstrap_iters` keys; Inspect scorer keys). Use
  `"unknown"` only when genuinely unidentifiable.

## generation_config
- `generation_args` is a **fixed, typed set**; anything else → `additional_details`.
- `reasoning` is a **bool**, not an effort level; there is no typed `effort`.

## timestamps
- `retrieved_timestamp` — required string epoch = when **this record** was created
  (**now**).
- `evaluation_timestamp` — when the eval ran (proxy with the source's date).
- Key `evaluation_id` on a **stable** value (eval time / dataset version) so reruns
  are idempotent. **Never key it on `now`.**
- **Leave optional fields unset rather than guess.**

## additional_details (everywhere)
- **`dict[str, str]`** — `json.dumps` numbers/bools/objects first, or validation fails.

## no typed home
- Per-score citation URL, alternate candidate scores, cost/token-$ — EEE has no
  fields; they land in `additional_details` (or are dropped).
