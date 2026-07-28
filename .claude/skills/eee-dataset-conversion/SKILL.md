---
name: eee-dataset-conversion
description: >-
  Convert an evaluation dataset or leaderboard into the Every Eval Ever (EEE)
  schema — aggregate `.json` logs (eval.schema.json) and optional instance
  `_samples.jsonl` sidecars (instance_level_eval.schema.json). Use when asked to
  write an EEE adapter, add a dataset/leaderboard to the EEE datastore, map
  benchmark results into EEE, or debug why an EEE record won't validate.
license: MIT
metadata:
  version: 0.1.0
---

# Converting evaluation results into Every Eval Ever (EEE)

> **Rule you will keep relearning: all records must validate, but validating ≠
> correct.** Most real defects (answer leakage, double-counted aggregates,
> hardcoded scorers, non-idempotent ids) pass the schema and are still wrong.
> Always spot-check *content*, not just validity.

*Written against EEE `SCHEMA_VERSION` `0.2.2` (import it from
`every_eval_ever.helpers`; never hardcode). If that value has moved, re-verify the
field claims in `reference/` against the live schema — the schema always wins.*

> **How this runs.** A person (the **operator**) runs you and can answer questions
> mid-run — you are **not fully autonomous**. When a choice *sets policy* (step 7's
> ask-list), **ask the operator** instead of deciding silently. Decide and **log**
> everything else. Finish with a PR that is **ready to merge** yet makes every
> non-obvious decision visible, so the **maintainer** who reviews it can comment and
> the skill/schema can improve. Two humans: the operator gates live; the PR informs
> the maintainer.

## When this skill applies
A source has model×benchmark scores (a leaderboard, a paper table, an HF results
dataset, a harness dump) and you must emit EEE records. Two artifacts:
- **Aggregate `.json`** — one `EvaluationLog` per model (or per model×benchmark),
  holding the headline scores. **Always produced.**
- **Instance `_samples.jsonl`** — one record per example. **Only** if you have
  per-item data and want it.

## Workflow (do these in order)
1. **Inspect the source first** — you can't map fields you haven't seen. Establish:
   distinct models · benchmarks/subtasks · the metric and its **range** · is there
   per-item data · the **harness** · timestamps · **provenance** (paper + each
   benchmark's own dataset repo). These facts are usually **spread across many
   surfaces** and which lives where varies per dataset, so **gather every *relevant*
   surface before recording a field as unknown** — see `reference/fields.md` §sources
   for the surface checklist, the coverage-vs-fill split, and which wins when they
   disagree. Filter hygiene junk (`.ipynb_checkpoints`, `*-checkpoint.json`) and
   segregate hand-curated baselines from harness runs.
2. **Decide the shape** — `source_type` (`documentation` if scraped vs
   `evaluation_run` if you ran it); aggregate-only vs +instances; grain (one log
   per model = default, or per model×benchmark when a benchmark has its own
   instance sidecar). See `reference/fields.md` §shape.
3. **Copy a template / reference adapter** — `templates/aggregate_adapter.py`
   (always) and, for per-item data, `templates/instance_sidecar.py` (runnable
   skeletons verified against the live validator). For a fuller real example,
   mirror `utils/llm_stats` (aggregate/documentation), `utils/hfopenllm_v2`
   (documentation, many models), or `utils/openeval` (aggregate + instance
   sidecars — the canonical write-order). Adapters run as
   `uv run python -m utils.<name>.adapter`; `__init__.py` just marks the package.
4. **Fill fields carefully** — the field traps are the whole game. Load
   `reference/fields.md` (aggregate) and `reference/instance-level.md` (jsonl).
5. **Canonicalize ids** — model + benchmark ids must resolve in the
   eval-card-registry (else they fragment the data). See `reference/registry.md`.
6. **Verify** — `python -m every_eval_ever validate <out>`, an offline unit test,
   ruff, a live smoke run, and a **content** spot-check. See `reference/verification.md`.
7. **Ask, then log your decisions.** Two channels, don't confuse them:
   - **Ask the operator (live)** when a choice *sets policy*: **creating a new
     canonical id · dropping a non-trivial share of the data · an ambiguous metric
     choice · bounding an unbounded metric · re-hosting large data.** Don't decide
     these silently — the person running you is there to answer.
   - **Log (in the PR)** every *non-obvious* choice — not just where it was hard. A
     confident wrong choice produces no "friction," so log **decisions**, not pain.
   Finish with a ready-to-merge PR carrying the decision log below. General gaps
   (would recur on other datasets) also become a separate `skill`-labeled PR or a
   `skill-gap` issue — you needn't know the fix; flagging where you guessed is enough.

### Decision log (paste into the PR — the PR template has a slot)
- **Decision / where** — the field or step (e.g. `source_data` for a DB dump).
- **Chose / instead of** — what you did and the alternative you rejected.
- **Confidence** — high / medium / low (low = please, maintainer, look here).
- **General?** — `yes` (→ `skill`/`skill-gap` PR/issue) or `no` (dataset-specific).
- **Coverage** (once per adapter) — "N source rows → N records, M dropped (reason)".
  **No silent caps** — if you filtered/sampled/capped anything, say so here.

## Load a reference only when you need it (progressive disclosure)
| Read this | When |
|---|---|
| `reference/fields.md` | Filling any aggregate field; "which of the 3 `source_*` / 3 `*_name` fields?" |
| `reference/instance-level.md` | Emitting `_samples.jsonl`: required fields, the `interaction_type` XOR, `sample_hash`, `answer_attribution`, the sidecar write-order |
| `reference/gotchas.md` | Something validates but looks wrong; `inf`, double-counting, CI optional-deps, big-parquet reads |
| `reference/registry.md` | Model/benchmark ids won't resolve; adding aliases |
| `reference/verification.md` | Before opening a PR; the checklist |

## The three PRs a contribution usually is
1. **Adapter code** → **this repo** (`utils/<name>/adapter.py` + `__init__.py`,
   a `README.md` (recommended), `tests/test_<name>_adapter.py`, + a row in `utils/README.md`).
2. **Canonical ids** → the `eval-card-registry` repo (aliases / new canonicals) —
   see its own `CONTRIBUTING.md` and the `registry-entity-aliases` skill there.
3. **Generated data** → the `EEE_datastore` HF dataset (`data/<name>/`, via a PR
   with `HfApi().upload_folder(..., create_pr=True)`).
Cross-link them.

Schemas are the source of truth — when a reference and the schema disagree, the
schema wins; read `eval.schema.json` / `instance_level_eval.schema.json`.
