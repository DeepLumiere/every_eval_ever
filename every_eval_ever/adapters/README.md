# Adapters

One-off adapter scripts that fetch leaderboard data from external sources and convert it to the Every Eval Ever schema. These are run manually, not via the main CLI.

## Writing a new adapter

Start from the `eee-dataset-conversion` agent skill —
[`.claude/skills/eee-dataset-conversion/SKILL.md`](../../.claude/skills/eee-dataset-conversion/SKILL.md).
It carries the field semantics, the merge-gate checks (`reference/datastore-gate.md`),
runnable templates, and the datastore submission mechanics. `tests/test_skill_conversion.py`
re-validates those templates against the live validator, so they stay current.

## Usage

Each adapter is run with `uv run python -m every_eval_ever.adapters.<name>.adapter`.

## Adapters

| Adapter | Data Source | Description |
|---------|-------------|-------------|
| `arc_agi` | ARC Prize leaderboard JSON | Converts ARC-AGI leaderboard data and merges canonical model aliases. |
| `artificial_analysis` | Artificial Analysis LLM API | Converts Artificial Analysis LLM benchmark, pricing, and performance results into `data/artificial-analysis-llms/`. |
| `vals_ai` | Vals.ai benchmark leaderboards | Scrapes Vals.ai benchmark pages and converts their embedded leaderboard results into `data/vals-ai/`. |
| `bfcl` | BFCL leaderboard CSV | Converts BFCL leaderboard data with per-metric evaluation names and bounded continuous scores. |
| `sciarena` | SciArena leaderboard API | Converts SciArena leaderboard results. |
| `global_mmlu_lite` | Kaggle API | Fetches Global MMLU Lite leaderboard results from Kaggle. |
| `hfopenllm_v2` | HuggingFace Spaces API | Fetches the Open LLM Leaderboard v2 (4576+ models). |
| `helm` | HELM leaderboard | Converts HELM leaderboard data. Supports `--leaderboard_name` for Capabilities/Lite/Classic/Instruct/MMLU. |
| `llm_stats` | LLM Stats API | Converts LLM Stats model, benchmark, and score API data into `data/llm-stats/`. |
| `mercor_eval` | Mercor Evaluation Exports API | Fetches authenticated Mercor benchmark leaderboards and writes aggregate EEE records. |
| `mt_bench` | LMSYS / FastChat | Converts MT-Bench GPT-4 single-answer judgments into `data/mt-bench/`. Emits overall, turn-1, and turn-2 means per model. |
| `openeval` | HuggingFace | Converts OpenEval response scores from `human-centered-eval/OpenEval` into `data/openeval/`; pass `--include-instances` to also write `*_samples.jsonl` sidecars. |
| `rewardbench` | HuggingFace | Fetches RewardBench v1 (CSV) and RewardBench v2 (JSON) leaderboard data. |
| `terminal_bench_2` | tbench.ai | Fetches Terminal-Bench 2.0 agentic coding benchmark results. |
| `hle` | Scale SEAL leaderboard | Converts the Scale SEAL Humanity's Last Exam leaderboard into `data/hle/`. Emits per-model accuracy (with 95% CI) and calibration error. |
| `mmlu_pro` | TIGER-Lab leaderboard CSV | Converts the MMLU-Pro leaderboard (`TIGER-Lab/mmlu_pro_leaderboard_submission`) into `data/mmlu-pro/`. Emits per-model overall + 14 per-subject accuracies. |
| `lexam` | LEXam project website | Converts the LEXam legal-reasoning leaderboard (open-question judge scores + 4-choice MCQ accuracy) into `data/lexam/`. |

### Mercor Evaluation Exports

Set the API key in the environment and run the adapter:

```bash
export MERCOR_EVAL_API_EVALEVAL_KEY="<your-key>"
uv run python -m every_eval_ever.adapters.mercor_eval.adapter
```

For a credential-free offline smoke run:

```bash
uv run python -m every_eval_ever.adapters.mercor_eval.adapter \
  --input-json tests/data/mercor_eval/api_payload.json \
  --output-dir /tmp/mercor-eval-offline
```

The adapter exports aggregate leaderboard metrics only. Mercor's criterion
results do not include the task input, model output, messages, or answer
attribution required by the EEE instance-level schema.
Records are generated under benchmark-specific datastore directories, for
example `data/apex-agents/<developer>/<model>/<uuid>.json`. Generated records
are intended for the Hugging Face datastore submission, not the GitHub adapter
PR.

### LEXam

```bash
uv run python -m every_eval_ever.adapters.lexam.adapter --output-dir data
```

The converter records only what the leaderboard publishes:

| Metric | Evaluation | Scope |
|---|---|---|
| Open Question Judge Score | `lexam.open_question` | `open_question` **test** split, n=2,541, graded by a pointwise-minimum ensemble of GPT-4o, DeepSeek-V3 and Qwen3-32B (human-expert validated) |
| Multiple-Choice Accuracy | `lexam.mcq_4_choices` | `mcq_4_choices`, n=1,655 — the site column reproduces the paper's MCQ-4 table and does **not** pool the 8/16/32-choice configs |

Provenance decisions worth knowing:

- `eval_library` names the harness (`lighteval`, version unknown), not the
  benchmark. The benchmark lives in `eval_library.additional_details`.
- `evaluator_relationship` is `third_party` — LEXam-Benchmark scores models it
  did not develop.
- The judge ensemble takes the **pointwise minimum** of three judges. The
  schema's `AggregationMethod` enum cannot express that, so no typed value is
  set and the method is recorded in `llm_scoring.additional_details`.
- `model_info.id` comes from the eval-card-registry;
  `model_info.additional_details.model_id_resolution` reports whether it came
  from a confirmed alias, a direct canonical match, or a Hugging Face id used
  because the registry has no entry for the evaluated checkpoint.
  `developer_org_id` carries the registry's normalized company org, which
  differs from the id prefix whenever the id is a Hugging Face repo id.
- `DeepSeek-V3.2-chat` and `DeepSeek-V3.2-reasoner` are the non-thinking and
  thinking modes of one release, so they share `model_info.id` and differ in
  `generation_config.generation_args.reasoning`.
- Inference settings and serving are **derived from the paper's own
  Reasoning / Large / Small bracketing of Table 1** (17/8/11) rather than left
  unknown: conventional models ran at temperature 0 with 4,096 tokens,
  reasoning models at 8,192 tokens on their official recommended settings, and
  appendix F gives the endpoints (local vLLM for the 7–14B conventional models,
  official APIs for the closed ones, Together AI for the rest).
- Every row names `lighteval`, on a LEXam author's confirmation for the current
  leaderboard rows (cited in `eval_library.additional_details.harness_source`).
  §3.3 says lighteval did not support the reasoning models at the time of
  writing, so those rows also carry that caveat in `harness_note` — the
  statement and its scope stay visible instead of collapsing into `unknown`.
- Where LEXam's own runner (`litellm_eval.py`) names a model, the exact served
  string and its sampling arguments are recorded in
  `model_info.additional_details.served_model` and `generation_args`. That
  config predates the post-paper leaderboard rows, so it covers 15 of 36
  models and nothing is extrapolated to the others. It disagrees with appendix
  F for `Gemma-3-12B-it` (Together AI vs local vLLM); `deployment_type` follows
  the paper and `served_model_note` records the conflict.
- Standard errors come from the paper's tables and are attached only while the
  scraped score still matches the score the paper reports.
- Metric ids, bounds and direction are the registry's, not the adapter's:
  `accuracy` is canonically a proportion on `[0,1]`, so the leaderboard's
  percentage is converted onto that scale (with the standard error), while the
  judge score keeps the `[0,100]` scale of its registry entry.
  `registry_snapshot.json` vendors just the entities this adapter emits, pinned
  to the registry revision they came from, and the tests fail if any of them
  drifts. Refresh it after a registry change:

  ```bash
  uv run python -m every_eval_ever.adapters.lexam.refresh_registry_snapshot \
      --registry /path/to/eval-card-registry
  ```

  Each metric reports the registry's own `review_status`, read from the
  snapshot, so a metric promoted from `draft` to `reviewed` upstream needs a
  refresh and no code change. `--check` answers "is the pin stale?" without
  writing — it exits non-zero and names both revisions, which is the thing to
  run after a registry PR merges:

  ```bash
  uv run python -m every_eval_ever.adapters.lexam.refresh_registry_snapshot \
      --registry /path/to/eval-card-registry --check
  ```

Submitting the generated records to the datastore (after the registry entities
are merged, so no record cites a `draft` metric):

```python
from huggingface_hub import HfApi

HfApi().upload_folder(
    folder_path='data/lexam',
    path_in_repo='data/lexam',
    repo_id='evaleval/EEE_datastore',
    repo_type='dataset',
    commit_message='[Submission] Add LEXam leaderboard',
    create_pr=True,
)
```

Record filenames are fresh uuids on every run, so a second `upload_folder` onto
an open submission PR *adds* another copy of every model rather than replacing
it. To update a submission, delete the folder and add the new records in one
commit:

```python
from pathlib import Path

from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi

records = sorted(Path('data/lexam').rglob('*.json'))
HfApi().create_commit(
    repo_id='evaleval/EEE_datastore',
    repo_type='dataset',
    revision='refs/pr/<n>',
    commit_message='Update LEXam records',
    operations=[
        CommitOperationDelete(path_in_repo='data/lexam/', is_folder=True),
        *(
            CommitOperationAdd(
                path_in_repo=f'data/lexam/{path.relative_to("data/lexam")}',
                path_or_fileobj=str(path),
            )
            for path in records
        ),
    ],
)
```


## Notes

- These are one-off scripts, not integrated into the main CLI.
- They require network access to fetch live leaderboard data.
- Some adapters (e.g. `rewardbench`, `helm`) may take several minutes to complete due to the number of models.
- Run `uv run python -m every_eval_ever.adapters.<name>.adapter --help` for adapter-specific options.
- Generated adapter outputs under `data/<source>/` and saved raw payloads are
  generated artifacts. Prefer temporary output paths for smoke runs unless a
  data refresh is intentionally part of the change.

### Legacy integrations

`arc_agi`, `livecodebenchpro`, and `mercor_eval` are retained for historical
and offline use, but their upstream sources are no longer usable for an active
refresh (`mercor_eval` currently returns an empty response). They are excluded
from active-adapter migration and compliance requirements. Deterministic
offline tests for their existing behavior may remain in the test suite.

### Partial conversions and provenance

An adapter may encounter a source row or metric that cannot be represented as
a valid EEE record—for example, a missing model identity or a non-numeric
score. It still writes every valid record. It also writes a strict JSON
provenance report under `adapter_reports/`, outside `data/`, with the source
reference, raw source fragment when available, and reason for each omission.
The command then exits non-zero so automation can distinguish a complete
refresh from a partial one.

Intentional non-evaluation rows, such as a published random baseline, are
recorded as exclusions in the same report but do not make the command fail.
The report is not an `EvaluationLog` and must not be passed to the validator.

### Vals.ai

Run a live smoke export from the repository root, writing generated output
outside the repo:

```bash
uv run python -m every_eval_ever.adapters.vals_ai.adapter \
  --output-dir /tmp/eee-vals-ai/data/vals-ai
```

To intentionally prepare a data refresh, use `--output-dir data/vals-ai` and
validate the result before deciding whether to include generated files.

For smaller smoke runs, fetch one benchmark:

```bash
uv run python -m every_eval_ever.adapters.vals_ai.adapter \
  --benchmark finance_agent \
  --output-dir /tmp/eee-vals-ai-smoke/data/vals-ai \
  --save-raw-json /tmp/eee-vals-ai-raw.json
```

Replay a saved normalized payload without hitting the network:

```bash
uv run python -m every_eval_ever.adapters.vals_ai.adapter \
  --input-json /tmp/eee-vals-ai-raw.json \
  --output-dir /tmp/eee-vals-ai-replay/data/vals-ai
```

Validate generated records with:

```bash
uv run python -m every_eval_ever validate \
  '/tmp/eee-vals-ai-smoke/data/vals-ai/*/*/*.json*'
```
