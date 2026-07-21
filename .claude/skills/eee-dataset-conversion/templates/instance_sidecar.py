"""Template: instance `_samples.jsonl` sidecar (single_turn Q&A shown).

Verified against utils/openeval + instance_level_eval.schema.json. Encodes the
messages/output XOR, answer_attribution-as-list, token_usage all-or-nothing, the
exact sample_hash recipe, and the save -> derive sidecar -> checksum -> re-write
sequence. `SCHEMA_VERSION` / `save_evaluation_log` are imported from
`every_eval_ever.helpers` (same as the aggregate adapter).
"""
import hashlib
import json

from every_eval_ever.eval_types import (
    DetailedEvaluationResults,
    Format,
    HashAlgorithm,
)
from every_eval_ever.helpers import SCHEMA_VERSION, save_evaluation_log
from every_eval_ever.instance_level_types import (
    AnswerAttributionItem,
    Evaluation,
    Input,
    InstanceLevelEvaluationLog,
    InteractionType,
    Output,
    TokenUsage,
)

SRC = "<src>"


def _sample_hash(raw: str, reference: list[str]) -> str:   # ONE recipe — every adapter MUST match
    payload = json.dumps({"raw": raw, "reference": reference},   # FULL list, not first elem / not a str
                         sort_keys=True, separators=(",", ":"))  # canonical JSON; [] when empty
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()   # == utils/openeval.sample_hash


def _instance(item, evaluation_id, evaluation_result_id, model_id):
    tok = None                                             # token_usage is ALL-OR-NOTHING:
    if item.in_tok is not None and item.out_tok is not None:   # build it only if you have all three
        tok = TokenUsage(input_tokens=item.in_tok, output_tokens=item.out_tok,
                         total_tokens=item.in_tok + item.out_tok)
    return InstanceLevelEvaluationLog(
        schema_version=SCHEMA_VERSION,                     # same "0.2.2" as the aggregate
        evaluation_id=evaluation_id,                       # REQUIRED FK: byte-identical to aggregate's
        evaluation_result_id=evaluation_result_id,         # FK to THIS evaluation_results[] row
        evaluation_name=f"{SRC}.{item.benchmark}",         # REQUIRED on the instance too (fallback FK)
        model_id=model_id,                                 # REQUIRED flat HF id == model_info.id
        sample_id=item.sample_id,                          # REQUIRED dataset id (e.g. gsm8k_0001)
        sample_hash=_sample_hash(item.raw, item.reference),# optional cross-model fallback for sample_id
        interaction_type=InteractionType.single_turn,      # -> output set, messages MUST stay null
        input=Input(raw=item.raw,                          # bare question: model-INDEPENDENT, answer-FREE
                    reference=item.reference,              # list[str], NOT a str
                    formatted=item.prompt),                # optional: chat-templated / few-shot string
        output=Output(raw=item.output),                    # list[str]; null for multi_turn/agentic
        answer_attribution=[AnswerAttributionItem(         # REQUIRED list; ALL 5 fields per item
            turn_idx=0, source="output.raw",               # 0 for single_turn
            extracted_value=item.parsed,                   # parsed answer (re-run scorer if source lacks it)
            extraction_method=item.scorer,                 # PLACEHOLDER: the scorer you ACTUALLY ran (e.g. "match")
            is_terminal=True)],                            # true = final answer
        evaluation=Evaluation(score=item.score,            # unconstrained float; 0.0/1.0 for binary
                              is_correct=item.is_correct),  # from the SOURCE score; binary-only meaningful
        token_usage=tok,                                   # None unless all three counts present
        metadata={"subject": str(item.subject)})           # extras go HERE; str values only


def export_with_instances(bundle, out_dir):                # resolves the two-write ordering
    agg_path = save_evaluation_log(bundle.log, out_dir,    # 1. save aggregate FIRST -> mints <uuid>.json
                                   bundle.developer, bundle.model)
    sidecar = agg_path.with_name(f"{agg_path.stem}_samples.jsonl")  # 2. matching-uuid sidecar name
    file_hash = hashlib.sha256()
    n = 0
    with sidecar.open("w", encoding="utf-8") as f:
        for item in bundle.items:
            # DEFAULT one-log-per-model grain: derive result_id PER ITEM so each line
            # attaches to the right aggregate result (== aggregate _result's id). A
            # single scalar (bundle.result_id) only fits per-(model, benchmark) grain.
            rec = _instance(item, bundle.log.evaluation_id,        # thread the SAME evaluation_id
                            f"{SRC}.{item.benchmark}", bundle.log.model_info.id)
            line = json.dumps(rec.model_dump(mode="json", exclude_none=True),
                              ensure_ascii=False) + "\n"
            file_hash.update(line.encode("utf-8"))         # checksum the EXACT bytes written
            f.write(line)
            n += 1
    bundle.log.detailed_evaluation_results = DetailedEvaluationResults(  # 3. attach the sidecar pointer
        format=Format.jsonl, file_path=sidecar.name,       # BASENAME only
        hash_algorithm=HashAlgorithm.sha256,               # REQUIRED to interpret the checksum
        checksum=file_hash.hexdigest(), total_rows=n)      # checksum over the file; total_rows = records
    agg_path.write_text(                                   # 4. RE-WRITE aggregate with the pointer
        bundle.log.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
