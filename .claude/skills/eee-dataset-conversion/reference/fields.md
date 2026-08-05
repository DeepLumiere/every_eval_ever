# Aggregate fields — what each holds, what confuses people, the fix

*Scope: aggregate `EvaluationLog` field semantics. Instance/jsonl fields →
`instance-level.md`; deeper failure modes → `gotchas.md`.*

Two collision clusters cause ~80% of mistakes:
- **"where from?" is three fields:** `source_metadata` (who *reported*),
  `source_data` (the *dataset*), the per-score *citation* (no typed home).
- **"what's its name?" is three fields:** `evaluation_name` (the eval),
  `metric_config.metric_name` (the metric), `source_metadata.source_name` (the venue).

## §sources — sweep every surface before calling a field missing
One eval is documented across many places, and **which fact lives where varies per
dataset** — so treat no location as fixed. A field is "unknown" only after you have
**swept the surfaces below** and it is on none of them; recorded-missing because you
checked one page is the failure this heads off. Fields most often blanked when the
answer *did* exist elsewhere: metric **range/definition**, the **harness**
(`eval_library`), `source_data` provenance (incl. `hf_repo`/`hf_split`), timestamps.

**Surfaces to sweep:** paper (PDF **and** arXiv HTML — the appendix often has the
metric definition) · GitHub README + `docs/` · the **results** dump/dataset · each
benchmark's **own** dataset repo · its HF dataset **card** · the HF **model** card ·
the leaderboard's pages/tabs/API · blog or release announcement.

**Coverage and fill are separate decisions — don't conflate them.**
- *Coverage:* take in every value relevant to the exact (model, benchmark, metric,
  run) you're converting, wherever it lives — the operator's pointer is a starting
  point, not the boundary. But **don't mix in irrelevant data** (a different model
  variant, another metric, a superseded arXiv version, a run under other settings).
  **Relevance** — not required-vs-optional, not effort — is the bound: you're done
  when the relevant surfaces are covered, and optional fields get the same thorough
  look as required ones.
- *Fill:* set a field **iff the sources you gathered actually provide it**; else
  **leave it empty** — emptiness reflects the sources, not how hard you looked. Never
  guess to fill; never skip a look because a field is "just optional."
  (Required fields can't simply be omitted — use the defined fallback where one
  exists, e.g. `eval_library: "unknown"`; if none fits, ask the operator, don't guess.)

A **gated or login-walled** surface is an **operator call**: don't self-authenticate
or accept a gate — flag what you couldn't reach.

When surfaces disagree, prefer the more primary (raw dump > paper > leaderboard >
blog) and **log which surface + its date/version** (arXiv vN, dataset revision,
leaderboard snapshot — none has a typed field, so it goes in the log or
`additional_details`) per contested value. Verify cheaply: `source_data` must be a
repo that actually exists, not just a name in a table.

## §shape — decide before writing code
1. **What artifact do you hold?** — this, not who ran the compute, sets `source_type`
   (who ran it is `evaluator_relationship`, a separate axis). Raw **per-item run
   outputs** → `evaluation_run` — *even when a third party ran them* (WILD: Kensho ran
   the evals and published the raw items → `evaluation_run` + `third_party`).
   **Only-aggregate reported numbers** → `documentation` — *even though a pipeline
   produced them* (a leaderboard/paper scrape stays `documentation`; "a run produced
   the leaderboard" does not make your scrape an `evaluation_run`). Item-level data is
   the strong tell for `evaluation_run`. See source_metadata.
2. **Aggregate-only or item-level?** headline per (model, benchmark) → aggregate
   `.json` (always); per-example too → instance `_samples.jsonl` (see
   `instance-level.md`).
3. **Grain?** default = **one log per model**, all benchmarks in
   `evaluation_results[]`; use **one log per (model, benchmark)** only when a
   benchmark has many subtasks and/or its own per-benchmark instance sidecar.

## source_metadata (per log)
- `source_type` — the **artifact you hold**, NOT who ran it: raw per-item run outputs
  → `evaluation_run` (even if a third party ran them); only-aggregate reported numbers
  → `documentation` (a leaderboard scrape stays `documentation` even though a pipeline
  produced the numbers). `evaluator_relationship` separately records WHO ran it. (The
  README/schema phrase it "run locally"; that under-specifies third-party raw runs —
  the artifact-you-hold test is what governs, and it agrees on every case in the
  datastore.)
- `source_name` — the **platform/leaderboard**, NOT the benchmark or author.
- `source_organization_name` — the **aggregator/publisher org**, NOT a username
  or the model developer.
- `evaluator_relationship` — relative to the **model developer**, not the reporter.
  A leaderboard running its own eval is still `third_party`. Enum:
  `first_party|third_party|collaborative|other` (no `self`).

## model_info
- `id` — the registry-**canonical** join key. Default: **resolve** the raw HF
  `developer/model` against the eval-card-registry resolver (hosted
  `POST /api/v1/resolve`, `entity_type:model`) and use its `canonical_id`; offer an
  opt-out flag (e.g. `--no-registry-resolve`) for speed/offline/determinism, and on
  opt-out or a network error fall back to the path id **marked unverified — never
  fatal**. Record resolution provenance (`strategy`/`confidence`/`created_new`/
  `review_status`) in `additional_details`, and surface auto-created-draft /
  low-confidence ids to the decision log (they hit the "new canonical id" ask). Don't
  invent ids; don't bake effort/mode/quant tiers in; dated snapshots are fine. See
  `registry.md`.
- **`evaluation_id` must NOT be keyed on the resolved id** — key it on the RAW source
  identity (path/repo + eval time). The registry may re-map a freshly auto-created
  draft later; a moving canonical id would break re-ingest idempotency. Resolved id =
  JOIN key (`model_info.id`); raw identity = RECORD identity (`evaluation_id`).
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
  (b) `continuous` **requires** `min_score`+`max_score`; (c) an **unbounded** metric
  is allowed — set `min_score`/`max_score` to `±inf` (the library serializes it as
  the JSON string `"Infinity"`; `null` means "not provided", not unbounded — see
  gotchas.md). (The JSON schema enforces (a); pydantic `validate` does
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
- Key `evaluation_id` on a **stable** value (eval time / dataset version / raw source
  id) so reruns are idempotent. **Never key it on `now`** — and never on the
  registry-resolved canonical id either (it can move; see model_info).
- **Leave optional fields unset rather than guess.**

## additional_details (everywhere)
- **`dict[str, str]`** — `json.dumps` numbers/bools/objects first, or validation fails.

## no typed home
- Per-score citation URL, alternate candidate scores, cost/token-$ — EEE has no
  fields; they land in `additional_details` (or are dropped).
