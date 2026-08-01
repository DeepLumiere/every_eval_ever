# Verification — the pre-PR gate

*Scope: the checks to run before opening a PR. One list — copy it into the PR description.*

- [ ] **Validate**: `python -m every_eval_ever validate <file.json> [<file.jsonl> ...]` (or a fixed-depth glob like `'data/<src>/**/*.json'`) → all pass (`.json`→aggregate, `.jsonl`→instance). Pass **files or a glob, not a directory** — the CLI rejects a bare dir. Run it on the files **at their final `data/<collection>/<dev>/<model>/` path**: this CLI runs the **semantic** checks the library `validate()` skips (path structure, companion pairing, `deployment_type`/`model_availability`), and those need the datastore context — a green in-library validate is necessary but not sufficient.
- [ ] **Offline unit test**: `pytest tests/test_<name>_adapter.py` — fixture-based, no network; guard optional deps (`pyarrow`/`inspect_ai`) with `pytest.importorskip` so `core` CI skips. Assert any **derived math** (e.g. `standard_error`, aggregate == mean of item scores) against a hand-computed value so it can't silently drift.
- [ ] **Full suite**: `pytest tests` — no regressions.
- [ ] **Lint**: `ruff check utils/<name>/ tests/test_<name>_adapter.py` — clean.
- [ ] **Live smoke run** on a slice → validate the real records.
- [ ] **Ids resolve**: model + benchmark ids resolve in the eval-card-registry (or the alias PR is prepared).
- [ ] **Content spot-check** (validating ≠ correct): `input.raw` doesn't leak the answer · aggregate not double-counted · `metric_name` is a metric not the eval · `source_data` is the dataset not the results · `evaluation_id` is stable (not keyed on `now`).
- [ ] **Decisions & coverage logged** (SKILL.md step 7): every non-obvious choice (+ the alternative, + confidence) is in the PR; coverage is stated as "N in → N records, M dropped (reason)" with no silent caps; the operator was asked about any policy call (new canonical id, big data drop, ambiguous/unbounded metric, re-hosting).
- [ ] **Instances** (if any): every line has `evaluation_id` (== aggregate), `model_id`, `evaluation_name`, `sample_id`; `answer_attribution` is a 5-field list; **a sample scored by K metrics = K records, one per aggregate result** (no single record spanning multiple results); `interaction_type` XOR holds.
- [ ] **Sidecar link**: `detailed_evaluation_results` has `hash_algorithm=sha256`, basename `file_path`, checksum over the written file, `total_rows` = record count; `sample_hash` uses the canonical-JSON recipe.

## Prove the skeleton (don't just eyeball it)
Construct one record exactly as the template prescribes, save it, and run the
validator. Syntax-OK ≠ schema-valid; a passing `validate` on a real constructed
record is the proof.
