"""Unit tests for the LEXam adapter."""

from pathlib import Path

import pytest

from every_eval_ever.adapters.lexam.adapter import (
    _MODEL_IDENTITIES,
    JUDGE_MODEL_IDS,
    MCQ_CONFIG,
    MCQ_METRIC,
    MCQ_SAMPLES,
    MCQ_SECTION_TITLE,
    OPEN_QUESTION_CONFIG,
    OPEN_QUESTION_METRIC,
    OPEN_QUESTIONS_SAMPLES,
    OPEN_SECTION_TITLE,
    REGISTRY_HARNESS,
    LEXamAdapter,
    _clean_model_name,
    _extract_section_rows,
    _model_identity,
    registry_snapshot,
)
from every_eval_ever.eval_types import EvaluationLog

FIXTURE_HTML = (
    Path(__file__).parent / 'data' / 'lexam' / 'leaderboard.html'
).read_text(encoding='utf-8')

OPEN_EVAL_NAME = f'lexam.{OPEN_QUESTION_CONFIG}'
MCQ_EVAL_NAME = f'lexam.{MCQ_CONFIG}'


def _gpt5_results() -> dict:
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    gpt5 = next(log for log in logs if log.model_info.name == 'GPT-5')
    return {r.evaluation_name: r for r in gpt5.evaluation_results}


def test_clean_model_name_strips_medals() -> None:
    assert _clean_model_name('GPT-5🥇') == 'GPT-5'
    assert _clean_model_name('Claude-3.7-Sonnet🥉') == 'Claude-3.7-Sonnet'


def test_extract_section_rows_open_questions() -> None:
    rows = _extract_section_rows(FIXTURE_HTML, OPEN_SECTION_TITLE)
    assert len(rows) == 3
    assert rows[0].model_name == 'GPT-5'
    assert rows[0].score == 70.20


def test_extract_section_rows_mcq() -> None:
    rows = _extract_section_rows(FIXTURE_HTML, MCQ_SECTION_TITLE)
    assert len(rows) == 3
    assert rows[2].model_name == 'Phi-4'
    assert rows[2].score == 25.0


def test_extract_section_rows_missing_section_raises() -> None:
    with pytest.raises(ValueError, match='Leaderboard section not found'):
        _extract_section_rows(FIXTURE_HTML, 'Missing Section')


def test_extract_section_rows_does_not_borrow_next_sections_table() -> None:
    """A heading whose own table is gone must fail, not read the next table."""
    open_start = FIXTURE_HTML.index('<table')
    open_end = FIXTURE_HTML.index('</table>') + len('</table>')
    without_open_table = FIXTURE_HTML[:open_start] + FIXTURE_HTML[open_end:]

    with pytest.raises(ValueError, match='No table found in section'):
        _extract_section_rows(without_open_table, OPEN_SECTION_TITLE)

    # The MCQ section is untouched and still parses.
    mcq_rows = _extract_section_rows(without_open_table, MCQ_SECTION_TITLE)
    assert [row.model_name for row in mcq_rows] == [
        'GPT-5',
        'GPT-4o-mini',
        'Phi-4',
    ]


def test_fetch_leaderboard_combines_metrics_per_model() -> None:
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    by_name = {log.model_info.name: log for log in logs}

    assert len(logs) == 4
    assert len(by_name['GPT-5'].evaluation_results) == 2
    assert len(by_name['Gemini-3-Pro-preview'].evaluation_results) == 1
    assert len(by_name['Phi-4'].evaluation_results) == 1


def test_scores_are_emitted_on_each_metrics_canonical_scale() -> None:
    """accuracy is a registry proportion; the judge score is a 0-100 slug."""
    results = _gpt5_results()
    open_result = results[OPEN_EVAL_NAME]
    mcq_result = results[MCQ_EVAL_NAME]

    # Judge score keeps the published 0-100 scale.
    assert open_result.score_details.score == 70.20
    assert open_result.metric_config.min_score == 0.0
    assert open_result.metric_config.max_score == 100.0
    assert open_result.metric_config.metric_unit == 'percent'

    # MCQ accuracy is rescaled onto the registry's [0, 1] accuracy scale.
    assert mcq_result.score_details.score == 0.6265
    assert mcq_result.metric_config.min_score == 0.0
    assert mcq_result.metric_config.max_score == 1.0
    assert mcq_result.metric_config.metric_unit == 'proportion'
    assert (
        mcq_result.score_details.details['leaderboard_reported_percent']
        == '62.65'
    )


def test_fetch_leaderboard_source_metadata_is_documentation() -> None:
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    assert logs[0].source_metadata.source_type.value == 'documentation'
    assert logs[0].source_metadata.source_name == 'LEXam Leaderboard'


def test_evaluator_relationship_is_third_party() -> None:
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    for log in logs:
        assert log.source_metadata.evaluator_relationship.value == 'third_party'


def test_eval_library_names_the_harness_not_the_benchmark() -> None:
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    assert logs[0].eval_library.name == 'lighteval'
    assert logs[0].eval_library.version == 'unknown'
    assert logs[0].eval_library.additional_details['benchmark'] == 'lexam'


def test_fetch_leaderboard_uses_hf_dataset_source() -> None:
    open_source = _gpt5_results()[OPEN_EVAL_NAME].source_data

    assert open_source.hf_repo == 'LEXam-Benchmark/LEXam'
    assert open_source.hf_split == 'test'
    assert open_source.samples_number == OPEN_QUESTIONS_SAMPLES == 2541
    assert open_source.additional_details['config'] == 'open_question'


def test_mcq_result_is_scoped_to_the_published_four_choice_config() -> None:
    """The leaderboard column covers mcq_4_choices only, not all 4,696 rows."""
    mcq_source = _gpt5_results()[MCQ_EVAL_NAME].source_data

    assert MCQ_CONFIG == 'mcq_4_choices'
    assert mcq_source.samples_number == MCQ_SAMPLES == 1655
    assert mcq_source.additional_details['config'] == 'mcq_4_choices'


def test_metric_ids_are_registry_canonical() -> None:
    """The schema asks for a canonical global id whenever one applies."""
    results = _gpt5_results()

    assert MCQ_METRIC.metric_id == 'accuracy'
    assert MCQ_METRIC.review_status == 'reviewed'
    assert results[MCQ_EVAL_NAME].metric_config.metric_id == 'accuracy'
    assert results[MCQ_EVAL_NAME].metric_config.metric_kind == 'accuracy'

    # No canonical global judge metric exists; this one is a registry-shaped
    # slug proposed alongside the adapter, not an ad-hoc namespaced id.
    assert OPEN_QUESTION_METRIC.metric_id == 'lexam-open-question-judge-score'
    # Read from the registry, not hand-set: flips on its own once upstream
    # promotes the entry.
    assert OPEN_QUESTION_METRIC.review_status in {'draft', 'reviewed'}
    assert results[OPEN_EVAL_NAME].metric_config.metric_kind == 'judge_score'
    for result in results.values():
        details = result.metric_config.additional_details
        assert details['bound_registry_revision']
        assert details['metric_registry_review_status'] in {
            'draft',
            'reviewed',
        }


def test_fetch_leaderboard_open_metric_has_llm_scoring() -> None:
    llm_scoring = _gpt5_results()[OPEN_EVAL_NAME].metric_config.llm_scoring

    assert llm_scoring is not None
    assert len(llm_scoring.judges) == 3
    assert {judge.model_info.id for judge in llm_scoring.judges} == {
        'openai/gpt-4o-2024-11-20',
        'deepseek-ai/DeepSeek-V3',
        'Qwen/Qwen3-32B',
    }


def test_judge_scoring_records_the_published_prompt_template() -> None:
    llm_scoring = _gpt5_results()[OPEN_EVAL_NAME].metric_config.llm_scoring

    assert '{question_fact}' in llm_scoring.input_prompt
    assert '{ref_answer}' in llm_scoring.input_prompt
    assert '{model_answer}' in llm_scoring.input_prompt
    assert 'Act as a Judge' in (
        llm_scoring.additional_details['judge_system_prompt']
    )


def test_judge_scoring_does_not_claim_average_aggregation() -> None:
    """LEXam takes the pointwise minimum; the enum cannot express that."""
    llm_scoring = _gpt5_results()[OPEN_EVAL_NAME].metric_config.llm_scoring

    assert llm_scoring.aggregation_method is None
    assert llm_scoring.additional_details['aggregation'] == 'pointwise_minimum'


def test_model_identities_are_resolved_not_invented() -> None:
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    gpt5 = next(log for log in logs if log.model_info.name == 'GPT-5')

    assert gpt5.model_info.id == 'openai/gpt-5'
    assert gpt5.model_info.developer == 'openai'
    details = gpt5.model_info.additional_details
    assert details['model_id_resolution'] == 'registry_alias'
    assert details['model_availability'] == 'closed_weights'
    assert details['developer_org_id'] == 'openai'
    assert details['leaderboard_label'] == 'GPT-5'


def test_every_identity_declares_availability_and_id_provenance() -> None:
    allowed_sources = {
        'registry_alias',
        'registry_canonical',
        'hf_canonical',
    }
    for label, identity in _MODEL_IDENTITIES.items():
        assert identity.availability in {'open_weights', 'closed_weights'}, label
        assert identity.id_source in allowed_sources, label
        assert identity.model_id.startswith(f'{identity.developer}/'), label
        assert identity.developer_org_id, label
        # No leaderboard display label leaks into an id as-is.
        assert identity.model_id != label


def test_no_registry_drift_against_the_vendored_snapshot() -> None:
    """Every registry-facing value must still match the pinned registry state.

    This is the guard that catches an invented metric id or a stale bound: the
    snapshot is regenerated from a registry checkout, so a drift shows up as a
    failing test and a reviewable diff rather than as silently wrong data.
    """
    snapshot = registry_snapshot()
    assert snapshot, 'registry_snapshot.json is missing'

    resolved = set(snapshot['models'])
    known_gaps = set(snapshot['models_absent_from_seed'])
    for label, identity in _MODEL_IDENTITIES.items():
        assert identity.model_id in resolved | known_gaps, label
    for judge_id in JUDGE_MODEL_IDS:
        assert judge_id in resolved | known_gaps, judge_id

    assert REGISTRY_HARNESS in snapshot['harnesses']
    assert not snapshot['metrics_unresolved']
    assert not snapshot['harnesses_unresolved']

    # evaluation_name must resolve to a canonical benchmark.
    for eval_name in (OPEN_EVAL_NAME, MCQ_EVAL_NAME):
        assert snapshot['benchmarks'].get(eval_name), eval_name

    # Bounds and direction are the registry's, not the adapter's.
    for spec in (MCQ_METRIC, OPEN_QUESTION_METRIC):
        entry = snapshot['metrics'][spec.metric_id]
        assert entry['min_score'] == spec.canonical_min, spec.metric_id
        assert entry['max_score'] == spec.canonical_max, spec.metric_id
        assert entry['lower_is_better'] is False, spec.metric_id
        assert entry['score_type'] == 'continuous', spec.metric_id


def test_developer_matches_the_datastore_path() -> None:
    """`developer` comes from the shared helper; the path comes from the id.

    They agree today for every LEXam model. If the helper ever normalizes an
    org differently (`Qwen` -> `alibaba`), this catches the record claiming one
    developer while landing in another's directory.
    """
    for label, identity in _MODEL_IDENTITIES.items():
        assert identity.developer == identity.model_id.split('/')[0], label


def test_one_retrieval_timestamp_per_run() -> None:
    """All records of a run describe the same retrieval of the page."""
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    assert len({log.retrieved_timestamp for log in logs}) == 1


def test_deepseek_api_modes_share_one_model_id() -> None:
    chat = _MODEL_IDENTITIES['DeepSeek-V3.2-chat']
    reasoner = _MODEL_IDENTITIES['DeepSeek-V3.2-reasoner']

    assert chat.model_id == reasoner.model_id == 'deepseek-ai/DeepSeek-V3.2'
    assert chat.reasoning is False
    assert reasoner.reasoning is True
    assert chat.api_model_name == 'deepseek-chat'
    assert reasoner.api_model_name == 'deepseek-reasoner'
    # The experimental release is a different checkpoint. Its id is the one
    # the registry resolves the label to, which is its API-catalog canonical
    # rather than the HF repo id — reconciling those is a registry-side pass.
    exp = _MODEL_IDENTITIES['DeepSeek-V3.2-Exp'].model_id
    assert exp == 'deepseek/deepseek-v3.2-exp'
    assert exp != chat.model_id


def test_reasoning_is_only_set_where_the_source_states_the_mode() -> None:
    with_reasoning = {
        label
        for label, identity in _MODEL_IDENTITIES.items()
        if identity.reasoning is not None
    }
    assert with_reasoning == {
        'DeepSeek-V3.2-chat',
        'DeepSeek-V3.2-reasoner',
    }


def test_standard_error_attached_only_when_score_still_matches() -> None:
    results = _gpt5_results()
    open_uncertainty = results[OPEN_EVAL_NAME].score_details.uncertainty
    assert open_uncertainty.standard_error.value == 0.41
    assert open_uncertainty.standard_error.method == 'bootstrap'
    assert open_uncertainty.num_samples == OPEN_QUESTIONS_SAMPLES
    # The MCQ standard error is rescaled with its score onto [0, 1].
    mcq_uncertainty = results[MCQ_EVAL_NAME].score_details.uncertainty
    assert mcq_uncertainty.standard_error.value == 0.0117
    assert 'arXiv' in results[OPEN_EVAL_NAME].score_details.details[
        'standard_error_source'
    ]

    # A score the paper never reported gets no standard error.
    moved = FIXTURE_HTML.replace('<strong>70.20</strong>', '<strong>70.21</strong>')
    logs = LEXamAdapter().fetch_leaderboard(html=moved)
    gpt5 = next(log for log in logs if log.model_info.name == 'GPT-5')
    open_result = next(
        r for r in gpt5.evaluation_results if r.evaluation_name == OPEN_EVAL_NAME
    )
    assert open_result.score_details.score == 70.21
    assert open_result.score_details.uncertainty.standard_error is None
    assert 'standard_error_source' not in open_result.score_details.details


def test_unmapped_row_is_reported_not_fatal() -> None:
    broken = FIXTURE_HTML.replace('Phi-4', 'Totally-New-Model')
    result = LEXamAdapter().fetch_leaderboard_result(html=broken)

    assert len(result.records) == 3
    assert len(result.failures) == 1
    assert 'Totally-New-Model' in result.failures[0].source_ref
    with pytest.raises(Exception, match='conversion issue'):
        result.raise_if_incomplete()


def test_evaluation_id_is_keyed_on_the_raw_leaderboard_label() -> None:
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    gpt5 = next(log for log in logs if log.model_info.name == 'GPT-5')
    assert gpt5.evaluation_id.startswith('lexam/GPT-5/')


def test_unknown_model_identity_raises() -> None:
    with pytest.raises(ValueError, match='No model identity mapping'):
        _model_identity('New-Unmapped-Model')


def test_fetch_leaderboard_output_validates_as_evaluation_log() -> None:
    logs = LEXamAdapter().fetch_leaderboard(html=FIXTURE_HTML)
    for log in logs:
        validated = EvaluationLog.model_validate(
            log.model_dump(mode='json', exclude_none=True)
        )
        assert validated.schema_version == log.schema_version
