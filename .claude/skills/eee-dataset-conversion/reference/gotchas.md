# Gotchas that cost real time (with fixes)

*Scope: deeper failure modes and mechanisms. For what each field **means**, see
`fields.md` / `instance-level.md`; this file is the "why it bit us" layer.*

- **`inf` bounds — settled: emit `±inf`, don't hand-roll it.** A genuinely unbounded
  `continuous` metric (PSNR, perplexity, WER) sets `min_score`/`max_score` to
  `float('-inf')`/`float('inf')`. The library serializes those as the JSON **strings**
  `"Infinity"`/`"-Infinity"` (a `field_serializer` on the bounds, every_eval_ever#212 —
  valid RFC-8259 JSON that reads back to a float), so just save through
  `save_evaluation_log`/`model_dump_json` and it round-trips + validates. Do **not**
  hand-roll `json.dumps(..., allow_nan=True)`: that writes a bare `Infinity` token,
  which the strict read path (#212) now **rejects**. `null` ≠ unbounded — `null` is
  "not provided" and still fails `continuous`. Give a **finite** bound only when a real
  nominal scale exists (`[0,1]`/`[0,100]`); don't invent one for an open-ended metric.
- **Model deployment axes (every_eval_ever#212).** `model_info.additional_details`
  must carry `deployment_type` ∈ `{self_deployed, externally_managed, unknown}` and
  `model_availability` ∈ `{open_weights, closed_weights, unknown}`. The library
  auto-fills both to `"unknown"`, so a green *library* `validate` does **not** mean you
  set them — but the CLI/bot `validate` (semantic checks) **errors** on a missing key
  or a non-enum value. Set the real value when you know it (a closed API model →
  `externally_managed`+`closed_weights`); don't ship an unconsidered `"unknown"`.
- **`score_type` omission — the `validate` false PASS.** `fields.md` says never omit
  it (omission fires the JSON-schema `levels` branch). Nuance: the repo's pydantic
  `validate` **passes** an omitted `score_type`, so a green `validate` does **not**
  prove you set it — the JSON schema is stricter. Same pydantic-vs-JSON-schema split
  as the non-string-dict trap below. Set it explicitly regardless.
- **Answer leakage into `input.raw`** — see `instance-level.md` (`input.raw`) for the
  detailed rule; in short, filter the source "conversation" to input roles only.
- **Aggregate vs parts double-counting** — emitting an overall *and* subtasks lets a
  consumer double-count. Mark the level; when a benchmark has ≤1 subtask emit **only**
  the overall.
- **micro vs macro** — emit the overall (micro, item-pooled) **and** every subtask
  with its `n` → both derivable downstream.
- **`additional_details` non-strings** → validation fail. Applies to instance
  string-maps too (`metadata`, `tool_calls[].arguments`, `performance.additional_details`).
  Trap: `validate` is pydantic (`dict[str, Any]`), so it will **not** flag non-strings
  that break the JSON schema.
- **Don't chase instance `metrics.num_turns`** — the schema's multi_turn `allOf`
  references a `metrics` property that doesn't exist; `num_turns` lives under
  `evaluation`. A top-level `metrics` object just trips `extra='forbid'`.
- **Non-idempotent `evaluation_id`** — keying on `now`/`retrieved_timestamp` changes
  every run. Key on a stable value; for an unparseable timestamp, derive a stable
  token from the source path, never `now`. For a **remote source** (HF/API), pin the
  dataset **commit SHA / revision** into the id so reruns match even if a live lookup
  hiccups — and **warn** rather than silently falling back to `now`. Reuse the *same*
  pinned revision across multiple passes (aggregate + instances) so they can't drift.
- **Reading big parquet/JSON over HTTP** — `datasets-server` may be empty; `duckdb`
  httpfs chokes on large string columns; **pyarrow `read_row_group(..., columns=[...])`**
  via `HfFileSystem` streams. Project only the small columns for aggregation.
- **CI optional deps** — a `core` test matrix installs no extras; if your adapter needs
  `pyarrow`/`inspect_ai`/etc., guard the test with `pytest.importorskip("pyarrow")` so
  `core` **skips** instead of failing collection. (An adapter using only stdlib +
  the core package needs no guard.) `importorskip` only covers CI — also **declare the
  optional dep** (a `<name>` extra) and note `--all-extras` in the adapter's README, so a
  *fresh local run* fails with a clear signal, not a cryptic top-level `ImportError`.
  **After adding/declaring any dependency, regenerate the lockfile (`uv lock`) and
  commit it** — the `locked` CI matrix installs from `uv.lock` frozen (`uv sync
  --locked`) and fails the moment it drifts from `pyproject.toml`, even though the
  `loose` jobs (which re-resolve) pass. A green `loose` + red `locked` almost always
  means a stale lockfile.
- **ruff** — the repo runs `ruff check` (E/F/I). Fix import order and `;` compounds
  (use `# noqa: E402` after an `importorskip` block).
- **Stale helpers** — `helpers.make_evaluation_log`/`make_evaluation_result` miss the
  now-required `eval_library`/per-result `source_data`; build the models by hand.
