"""Template: minimal aggregate EEE adapter (one score per (model, benchmark)).

Copy into utils/<name>/adapter.py and replace <src>/<Platform> and the fetch.
Mirror utils/llm_stats for anything more complex. Build models BY HAND — the
helpers.make_* functions are stale (miss eval_library / per-result source_data).
"""
from dataclasses import dataclass

from every_eval_ever.eval_types import (
    EvalLibrary,
    EvaluationLog,
    EvaluationResult,
    EvaluatorRelationship,
    MetricConfig,
    ModelInfo,
    ScoreDetails,
    ScoreType,
    SourceDataUrl,
    SourceMetadata,
)
from every_eval_ever.helpers import SCHEMA_VERSION, save_evaluation_log

SRC = "<src>"


@dataclass(frozen=True)
class LogBundle:
    log: EvaluationLog
    developer: str
    model: str
    # instance path only (see instance_sidecar.py); leave empty for aggregate-only:
    items: tuple = ()          # per-item objects to emit as _samples.jsonl
    result_id: str = ""        # only fits per-(model, benchmark) grain; for the
                               # default one-log-per-model grain derive result_id
                               # per item instead (see instance_sidecar._instance)


def _result(benchmark, score, dataset_url):        # one benchmark -> one result
    return EvaluationResult(
        evaluation_result_id=f"{SRC}.{benchmark}",     # stable join key; instances point here
        evaluation_name=f"{SRC}.{benchmark}",          # namespaced id, NOT a title
        source_data=SourceDataUrl(dataset_name=benchmark, source_type="url",
                                  # DATASET url ONLY -> a paper/leaderboard citation is NOT
                                  # dataset provenance and has no typed home; put it in
                                  # additional_details, never here (fields.md "no typed home").
                                  url=[dataset_url]),
        metric_config=MetricConfig(
            # Describes an ACCURACY metric (0-1, higher-is-better). CHANGE every field for
            # your metric -> name/kind/unit/direction/bounds; else you emit valid-but-wrong
            # metadata (validating != correct; see the metric_config notes in fields.md).
            metric_name="accuracy", metric_kind="accuracy", metric_unit="proportion",
            lower_is_better=False, score_type=ScoreType.continuous,  # never omit score_type
            min_score=0.0, max_score=1.0),               # continuous REQUIRES finite min/max
        score_details=ScoreDetails(score=score))


def make_log(model, developer, results, eval_ts, retrieved_ts):   # DEFAULT: 1 log / model
    return EvaluationLog(
        schema_version=SCHEMA_VERSION,
        # STABLE anchor -> idempotent (NOT now). If the source is MUTABLE (re-scraped, a
        # live leaderboard), fold a source revision/run-id into this key; eval_ts alone
        # can collide across changed snapshots (fields.md timestamps).
        evaluation_id=f"{SRC}/{developer}_{model}/{eval_ts}",
        retrieved_timestamp=retrieved_ts,                 # STRING epoch = now (record-creation)
        evaluation_timestamp=eval_ts,                     # when the eval ran
        source_metadata=SourceMetadata(
            source_name="<Platform>", source_type="documentation",
            source_organization_name="<Aggregator org>",  # NOT the model dev / a username
            evaluator_relationship=EvaluatorRelationship.third_party,
            additional_details={"source_role": "aggregator"}),   # str values only
        # name the harness if the format reveals it (lm-eval/inspect); else "unknown":
        eval_library=EvalLibrary(name="unknown", version="unknown"),
        model_info=ModelInfo(name=model, id=f"{developer}/{model}"),  # canonicalize via registry
        evaluation_results=[_result(b, s, u) for (b, s, u) in results])


def fetch_rows(args):
    """PLACEHOLDER — replace with your source fetch. Yield one tuple per model:
    (model, developer, [(benchmark, score, dataset_url), ...], eval_ts)."""
    raise NotImplementedError("wire up the source fetch")


def parse_args():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="data/<src>")
    ap.add_argument("--limit", type=int, default=None)
    return ap.parse_args()


def run(args):
    import time
    retrieved_ts = str(time.time())                       # record-creation time = now
    written = 0
    for model, developer, results, eval_ts in fetch_rows(args):
        log = make_log(model, developer, results, eval_ts, retrieved_ts)
        save_evaluation_log(log, args.output_dir, developer, model)
        written += 1
    print(f"wrote {written} logs -> {args.output_dir}")
    return written


if __name__ == "__main__":            # run:  uv run python -m utils.<name>.adapter
    run(parse_args())
    # then validate:  python -m every_eval_ever validate <output-dir>
