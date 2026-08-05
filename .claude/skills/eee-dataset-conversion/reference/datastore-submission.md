# Submitting to the `EEE_datastore` HF dataset

*Scope: the mechanics of the data PR. What makes a record valid is
`datastore-gate.md`; what a field means is `fields.md`. Everything here is drawn from
how real submissions actually went — most rework was mechanical, not semantic.*

## Layout: one collection per source
- `data/<collection>/<developer>/<model>/<uuid>.json` (+ `<uuid>_samples.jsonl`).
- **`<collection>` names the source you converted, not the benchmark being run.** An
  adapter that emits one directory per benchmark (`data/gaia/`, `data/usaco/`) collides
  with other sources' collections and hides which leaderboard the numbers came from. The
  benchmark belongs in `evaluation_name` / `source_data`. Remember the collection is
  derived from `evaluation_results[0].source_data.dataset_name` unless you pass
  `collection_override` — set it deliberately.
- If a source has thousands of sub-leaderboards with colliding slugs, prefix them
  (`<owner>__<slug>`) so the namespace stays unique.
- The **code** never goes here. Adapter code belongs in `every_eval_ever/adapters/<name>/`
  in the GitHub repo — reviewers ask for it every time data shows up without it. Link the
  two PRs to each other.

## Upload
- `HfApi().upload_folder(..., repo_type="dataset", create_pr=True)`.
- **Batch large submissions.** A single commit with thousands of files can 504 — the
  commit may land server-side while the client errors, leaving a half-submitted PR you
  then have to abandon. Upload in chunks (a few hundred files), and say `(n/N)` in the
  title so reviewers know the set is incomplete until the last batch lands.
- Generated records are **never** committed to the code repo. Point smoke runs at a temp
  dir (`--output-dir /tmp/...`); writing into `data/` in the checkout is only for a
  deliberate refresh.

## Iterate on the SAME PR — don't open a new one
Push new commits onto the existing PR ref (`refs/pr/<n>`). Opening a fresh PR for each
round of bot warnings is the dominant source of churn in this datastore's history — one
submission needed five PRs to clear a single `deployment_type` warning, another five to
land one benchmark. Reviewers lose the thread and duplicate discussions.

## The review bot
- Comment `/eee validate changed` to (re)run validation on the PR's changed files. There
  is a short cooldown (~a minute); repeated commands are ignored, not queued.
- **A "Ready to Merge" verdict can still carry warnings.** Read them; the common ones are
  missing `deployment_type` / `model_availability`, `source_type: hf_dataset` without
  `hf_repo`, and `source_type: other` with no URL provenance. Fix them in the same PR
  before asking for review.
- The bot reports its own **compatibility version**. If it differs from your local
  `SCHEMA_VERSION`, expect vocabulary skew (see `datastore-gate.md` §deployment); ask the
  maintainers rather than downgrading records to match an older gate.

## What the PR description needs
- **Source** — the leaderboard/paper, the *dataset* the eval ran on, and a **pinned
  revision** (commit SHA / dataset revision / snapshot date), not a mutable `main`.
- **Coverage** — "N source rows → M records, K dropped (reason)". Expect to be asked
  "what about the other models?"; answer it up front, and name any cap or sample you
  applied. The `adapter_reports/<collection>_failures.json` your adapter wrote is the
  evidence for this line.
- **Cross-links** — the adapter PR in `every_eval_ever`, and any alias PR in
  `eval-card-registry`.
- **Decisions** — the non-obvious calls, the alternative you rejected, and your
  confidence (SKILL.md step 7). Low confidence is a request for maintainer attention, not
  an admission of failure.
- **Instances** — if you shipped `_samples.jsonl`, say why (and if you deliberately did
  not re-host public raw data, say that too).
