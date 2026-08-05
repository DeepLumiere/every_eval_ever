"""Adapter for converting LEXam public leaderboard HTML to every_eval_ever format.

Scope of the published leaderboard (https://lexam-benchmark.github.io/):

* "Judge Scores on Open Questions" — mean judge score over the
  `open_question` **test** split (n=2,541), scored by a pointwise-minimum
  ensemble of GPT-4o, DeepSeek-V3 and Qwen3-32B.
* "Accuracy on Multiple-Choice Questions" — accuracy on `mcq_4_choices`
  (n=1,655) only. It is *not* pooled over the 8/16/32-choice configs.

Known identity limitations, kept visible in
`model_info.additional_details.model_id_resolution`:

* `DeepSeek-V3.2-chat` / `DeepSeek-V3.2-reasoner` are DeepSeek API *modes*
  rather than separately released checkpoints, so neither label maps onto one
  canonical model; both are marked `unverified`.
* `Llama-3.1-405B-it` and `EuroLLM-9B-it` have no eval-card-registry entry for
  the evaluated variant; the canonical Hugging Face repo id is used
  (`hf_canonical`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

from every_eval_ever.converters import SCHEMA_VERSION
from every_eval_ever.converters.common.utils import get_current_unix_timestamp
from every_eval_ever.eval_types import (
    EvalLibrary,
    EvaluationLog,
    EvaluationResult,
    EvaluatorRelationship,
    JudgeConfig,
    LlmScoring,
    MetricConfig,
    ModelInfo,
    ScoreDetails,
    ScoreType,
    SourceDataHf,
    SourceMetadata,
    SourceType,
    Uncertainty,
)

logger = logging.getLogger(__name__)

LEADERBOARD_URL = (
    'https://raw.githubusercontent.com/LEXam-Benchmark/'
    'lexam-benchmark.github.io/main/index.html'
)
LEADERBOARD_PAGE_URL = 'https://lexam-benchmark.github.io/'
HF_REPO = 'LEXam-Benchmark/LEXam'
GITHUB_REPO_URL = 'https://github.com/LEXam-Benchmark/LEXam'
PAPER_URL = 'https://arxiv.org/abs/2505.12864'
BENCHMARK_KEY = 'lexam'

# Open questions: the leaderboard scores the `open_question` *test* split
# (paper appendix B.2: test 2,541 / dev 300).
OPEN_QUESTION_CONFIG = 'open_question'
OPEN_QUESTIONS_SAMPLES = 2541

# The MCQ leaderboard publishes the 4-choice configuration only, not the union
# of the four MCQ configs. The site's "Accuracy on Multiple-Choice Questions"
# column reproduces the paper's MCQ-4 table verbatim (GPT-5 62.65,
# Claude-4.5-Sonnet 58.01, GPT-4.1 54.40, GPT-4o-mini 40.96, ...), and the
# published bootstrap standard errors (±1.17 at p≈0.63) are consistent with
# n=1,655, not with the 4,696-row union.
MCQ_CONFIG = 'mcq_4_choices'
MCQ_SAMPLES = 1655

OPEN_SECTION_TITLE = 'Leaderboard on LEXam – Open Questions'
MCQ_SECTION_TITLE = 'Leaderboard on LEXam – Multiple-Choice Questions'

# Judge prompt published in the LEXam repository
# (`customized_judge_async.py`, identical to the `20250324` template used by
# the lighteval community task `lexam_oq_evals.py`).
JUDGE_SYSTEM_PROMPT = (
    'Act as a Judge specializing in the evaluation of Swiss law schools '
    'exams. Your task is to assess how well the response aligns with the '
    'reference answer, with a focus on accuracy, completeness, and legal '
    'reasoning.'
)

JUDGE_USER_PROMPT_TEMPLATE = """Goal:
Your task is to assess how well the response aligns with the reference answer, with a focus on accuracy, completeness, and legal reasoning.

Context:
You will be provided with a response (labeled: Model's Answer) to a law school exam question (labeled: Question) and a reference answer (labeled: Reference Answer).

Return format:
    After reviewing the response:
    1. Explanation: Briefly explain your reasoning regarding how the response conforms to or deviates from the reference answer.
    2. Constructive feedback: Additionally, provide neutral, constructive feedback and corrections in the style of a university professor.
    3. Correctness score: Assign a final correctness score on a scale from 0.0 to 1.0 (in increments of 0.1). This score should reflect the extent to which the response satisfies the reference answer, where
        - 1.0 = complete fulfillment (100%)
        - lower scores reflect proportionate shortfalls (e.g. 0.5 = 50% fulfillment).
        - strictly follow the format: \"[[score]]\", e.g., \"The correctness score: [[0.5]]\".

Warnings:
    - In some cases, the reference answer may include only keywords or factual elements to be examined, along with (+), (-) or (+/-). Respect these indications when determining correctness:
        - (+) means the element must be affirmed.
        - (–) means the element must be denied.
        - (-/+) indicates that arguments in either direction are acceptable if legally sound.
    - Deviations or additional elements not found in the reference answer should generally be penalized unless you are certain they are legally correct and relevant. Assume the reference answer includes all information necessary for a perfect response.
    - The reference answer may contain citations (e.g., from books or law review articles), which the response does not need to replicate. However, statutes should be cited precisely, specifying Abs., Ziff., or lit. whenever applicable.
    - If the reference answer includes separate sub-points, use these for proportional scoring guidance (e.g., addressing 2 out of 4 sub-points correctly equals approximately a 0.5 score).
Judge the below case, give the brief reasoning process and the final grade.


Question:
```{question_fact}```

Reference Answer:
```{ref_answer}```

Model's Answer:
```[{model_answer}]```

Your Judgment:
"""

# The schema's AggregationMethod enum offers majority_vote / average /
# weighted_average / median. LEXam aggregates the *pointwise minimum* across
# its three judges (paper §4: "we adopt an minimum-score ensemble of GPT-4o,
# Qwen3-32B, and DeepSeek-V3"; "By aggregating pointwise minimum scores"), so
# no typed value is correct and the method is recorded in additional_details
# instead of asserting a wrong one.
JUDGE_AGGREGATION = 'pointwise_minimum'

_MEDAL_RE = re.compile(r'[\U0001f947-\U0001f949]')


@dataclass(frozen=True)
class LeaderboardRow:
    """A single model row from a LEXam leaderboard table."""

    model_name: str
    score: float


@dataclass(frozen=True)
class ModelIdentity:
    """Model identity for a LEXam leaderboard label.

    `model_id` is the join key written to `model_info.id`. `id_source` records
    how it was obtained so consumers can tell a registry-backed id from a
    best-effort one:

    * `registry_alias` — a confirmed eval-card-registry alias maps the
      leaderboard label to this canonical id.
    * `registry_canonical` — the label matches an existing reviewed canonical
      id one-to-one; the alias itself is not registered yet.
    * `hf_canonical` — the registry has no entry for the evaluated variant;
      the id is the canonical Hugging Face repo id.
    * `unverified` — the leaderboard label does not identify a single released
      variant (see the module docstring notes); kept as published.
    """

    developer: str
    model_id: str
    availability: str
    id_source: str


_MODEL_IDENTITIES = {
    'Apertus-70B': ModelIdentity(
        developer='swiss-ai-initiative',
        model_id='swiss-ai-initiative/apertus-70b',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Apertus-8B': ModelIdentity(
        developer='swiss-ai-initiative',
        model_id='swiss-ai-initiative/apertus-8b',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Claude-3.7-Sonnet': ModelIdentity(
        developer='anthropic',
        model_id='anthropic/Claude-3.7-Sonnet',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'Claude-4.5-Sonnet': ModelIdentity(
        developer='anthropic',
        model_id='anthropic/claude-sonnet-4.5',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'DeepSeek-R1': ModelIdentity(
        developer='deepseek-ai',
        model_id='deepseek-ai/DeepSeek-R1',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'DeepSeek-V3': ModelIdentity(
        developer='deepseek-ai',
        model_id='deepseek-ai/DeepSeek-V3',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'DeepSeek-V3.2-Exp': ModelIdentity(
        developer='deepseek',
        model_id='deepseek/deepseek-v3-2-exp',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'DeepSeek-V3.2-chat': ModelIdentity(
        developer='deepseek-ai',
        model_id='deepseek-ai/DeepSeek-V3.2-chat',
        availability='open_weights',
        id_source='unverified',
    ),
    'DeepSeek-V3.2-reasoner': ModelIdentity(
        developer='deepseek-ai',
        model_id='deepseek-ai/DeepSeek-V3.2-reasoner',
        availability='open_weights',
        id_source='unverified',
    ),
    'EuroLLM-9B-it': ModelIdentity(
        developer='utter-project',
        model_id='utter-project/EuroLLM-9B-Instruct',
        availability='open_weights',
        id_source='hf_canonical',
    ),
    'GPT-4.1': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-4.1',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-4.1-mini': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-4.1-mini',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-4.1-nano': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-4.1-nano',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-4o': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-4o',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-4o-mini': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-4o-mini',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-5': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-5',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-5-mini': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-5-mini',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-5-nano': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-5-nano',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-OSS-120B': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-oss-120b',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'GPT-OSS-20B': ModelIdentity(
        developer='openai',
        model_id='openai/gpt-oss-20b',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Gemini-2.5-Pro': ModelIdentity(
        developer='google',
        model_id='google/gemini-2.5-pro',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'Gemini-3-Pro-preview': ModelIdentity(
        developer='google',
        model_id='google/gemini-3-pro-preview',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'Gemma-2-9B-it': ModelIdentity(
        developer='google',
        model_id='google/gemma-2-9b-it',
        availability='open_weights',
        id_source='registry_canonical',
    ),
    'Gemma-3-12B-it': ModelIdentity(
        developer='google',
        model_id='google/gemma-3-12b-it',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Llama-3.1-405B-it': ModelIdentity(
        developer='meta-llama',
        model_id='meta-llama/Llama-3.1-405B-Instruct',
        availability='open_weights',
        id_source='hf_canonical',
    ),
    'Llama-3.1-8B-it': ModelIdentity(
        developer='meta-llama',
        model_id='meta-llama/Llama-3.1-8B-Instruct',
        availability='open_weights',
        id_source='registry_canonical',
    ),
    'Llama-3.3-70B-it': ModelIdentity(
        developer='meta-llama',
        model_id='meta-llama/Llama-3.3-70B-Instruct',
        availability='open_weights',
        id_source='registry_canonical',
    ),
    'Llama-4-Maverick': ModelIdentity(
        developer='meta-llama',
        model_id='meta-llama/Llama-4-Maverick-17B-128E',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Ministral-8B-it': ModelIdentity(
        developer='mistralai',
        model_id='mistralai/ministral-8b-it',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'O3-mini': ModelIdentity(
        developer='openai',
        model_id='openai/o3-mini',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'Phi-4': ModelIdentity(
        developer='microsoft',
        model_id='microsoft/phi-4',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'QwQ-32B': ModelIdentity(
        developer='Qwen',
        model_id='Qwen/QwQ-32B',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Qwen-2.5-7B-it': ModelIdentity(
        developer='Qwen',
        model_id='Qwen/Qwen2.5-7B-Instruct',
        availability='open_weights',
        id_source='registry_canonical',
    ),
    'Qwen3-235B': ModelIdentity(
        developer='Qwen',
        model_id='Qwen/Qwen3-235B-A22B',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Qwen3-32B': ModelIdentity(
        developer='Qwen',
        model_id='Qwen/Qwen3-32B',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Qwen3-Next': ModelIdentity(
        developer='alibaba',
        model_id='alibaba/qwen3-next',
        availability='open_weights',
        id_source='registry_alias',
    ),
}


def _fetch_html(url: str = LEADERBOARD_URL) -> str:
    """Download leaderboard HTML from *url*."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _clean_model_name(raw_name: str) -> str:
    """Strip medal glyphs and whitespace from a leaderboard model name."""
    return _MEDAL_RE.sub('', raw_name).strip()


def _model_identity(model_name: str) -> ModelIdentity:
    """Return the explicit model identity for a LEXam leaderboard name."""
    if model_name not in _MODEL_IDENTITIES:
        raise ValueError(
            f'No model identity mapping for LEXam leaderboard model: {model_name}'
        )
    return _MODEL_IDENTITIES[model_name]


def _extract_section_rows(
    html: str, section_title: str
) -> list[LeaderboardRow]:
    """Parse model/score rows from the table under *section_title*.

    The search is bounded by the next section heading so a section whose own
    table is absent cannot silently consume the following section's table
    (which would publish, for example, MCQ accuracies as judge scores).
    """
    title_idx = html.find(section_title)
    if title_idx == -1:
        raise ValueError(f'Leaderboard section not found: {section_title}')

    heading_end = html.find('</h1>', title_idx)
    search_from = title_idx if heading_end == -1 else heading_end
    next_heading = html.find('<h1', search_from)
    section_end = len(html) if next_heading == -1 else next_heading

    table_start = html.find('<table', search_from, section_end)
    if table_start == -1:
        raise ValueError(f'No table found in section: {section_title}')

    table_end = html.find('</table>', table_start, section_end)
    if table_end == -1:
        raise ValueError(
            f'Table not closed within section: {section_title}'
        )

    table_html = html[table_start:table_end]
    row_re = re.compile(
        r'<tr[^>]*>\s*'
        r'<td[^>]*>(?:<strong>)?(\d+)(?:</strong>)?</td>\s*'
        r'<td[^>]*>(.*?)</td>\s*'
        r'<td[^>]*>(?:<strong>)?([\d.]+)(?:</strong>)?</td>\s*'
        r'</tr>',
        re.DOTALL | re.IGNORECASE,
    )

    rows: list[LeaderboardRow] = []
    for row_match in row_re.finditer(table_html):
        _, model_cell, score_text = row_match.groups()
        model_name = re.sub(r'<[^>]+>', '', model_cell)
        model_name = _clean_model_name(model_name)
        if not model_name:
            continue
        rows.append(
            LeaderboardRow(
                model_name=model_name,
                score=float(score_text),
            )
        )
    if not rows:
        raise ValueError(f'No leaderboard rows found for: {section_title}')
    return rows


def _open_question_source() -> SourceDataHf:
    return SourceDataHf(
        dataset_name=BENCHMARK_KEY,
        source_type='hf_dataset',
        hf_repo=HF_REPO,
        hf_split='test',
        samples_number=OPEN_QUESTIONS_SAMPLES,
        additional_details={
            'benchmark_section': 'open_questions',
            'config': OPEN_QUESTION_CONFIG,
        },
    )


def _mcq_source() -> SourceDataHf:
    return SourceDataHf(
        dataset_name=BENCHMARK_KEY,
        source_type='hf_dataset',
        hf_repo=HF_REPO,
        hf_split='test',
        samples_number=MCQ_SAMPLES,
        additional_details={
            'benchmark_section': 'multiple_choice_questions',
            'config': MCQ_CONFIG,
        },
    )


def _judge_model_info(
    name: str, model_id: str, availability: str, deployment: str
) -> ModelInfo:
    return ModelInfo(
        name=name,
        id=model_id,
        developer=model_id.split('/')[0],
        additional_details={
            'deployment_type': deployment,
            'model_availability': availability,
        },
    )


def _open_question_judge_scoring() -> LlmScoring:
    return LlmScoring(
        judges=[
            JudgeConfig(
                model_info=_judge_model_info(
                    'gpt-4o',
                    'openai/gpt-4o-2024-11-20',
                    'closed_weights',
                    'externally_managed',
                ),
            ),
            JudgeConfig(
                model_info=_judge_model_info(
                    'DeepSeek-V3',
                    'deepseek-ai/DeepSeek-V3',
                    'open_weights',
                    'unknown',
                ),
            ),
            JudgeConfig(
                model_info=_judge_model_info(
                    'Qwen3-32B',
                    'Qwen/Qwen3-32B',
                    'open_weights',
                    'unknown',
                ),
            ),
        ],
        input_prompt=JUDGE_USER_PROMPT_TEMPLATE,
        additional_details={
            'judge_system_prompt': JUDGE_SYSTEM_PROMPT,
            'judge_prompt_version': '20250324',
            'aggregation': JUDGE_AGGREGATION,
            'aggregation_note': (
                'Pointwise minimum of the three judge scores per question, '
                'then averaged over questions. The schema '
                'AggregationMethod enum has no minimum option, so no typed '
                'value is set.'
            ),
            'validation': 'human expert validated',
            'source': f'{GITHUB_REPO_URL}/blob/main/customized_judge_async.py',
            'citation': PAPER_URL,
        },
    )


def _build_open_question_result(score: float) -> EvaluationResult:
    return EvaluationResult(
        evaluation_name=f'{BENCHMARK_KEY}.{OPEN_QUESTION_CONFIG}',
        metric_config=MetricConfig(
            metric_id='lexam.open_question_judge_score',
            metric_name='Open Question Judge Score',
            metric_kind='judge_score',
            metric_unit='percent',
            evaluation_description=(
                'Mean LLM-judge score on the open-ended law exam questions '
                f'of the LEXam {OPEN_QUESTION_CONFIG} test split '
                f'(n={OPEN_QUESTIONS_SAMPLES}), scored by a pointwise-minimum '
                'ensemble of three judges (0-100 scale).'
            ),
            lower_is_better=False,
            score_type=ScoreType.continuous,
            min_score=0.0,
            max_score=100.0,
            llm_scoring=_open_question_judge_scoring(),
        ),
        score_details=ScoreDetails(
            score=round(score, 2),
            uncertainty=Uncertainty(num_samples=OPEN_QUESTIONS_SAMPLES),
        ),
        source_data=_open_question_source(),
    )


def _build_mcq_result(score: float) -> EvaluationResult:
    return EvaluationResult(
        evaluation_name=f'{BENCHMARK_KEY}.{MCQ_CONFIG}',
        metric_config=MetricConfig(
            metric_id='lexam.mcq_accuracy',
            metric_name='Multiple-Choice Accuracy',
            metric_kind='accuracy',
            metric_unit='percent',
            evaluation_description=(
                'Accuracy on the LEXam four-choice multiple-choice questions '
                f'({MCQ_CONFIG}, n={MCQ_SAMPLES}); the published leaderboard '
                'column does not cover the 8/16/32-choice configs '
                '(0-100 scale).'
            ),
            lower_is_better=False,
            score_type=ScoreType.continuous,
            min_score=0.0,
            max_score=100.0,
        ),
        score_details=ScoreDetails(
            score=round(score, 2),
            uncertainty=Uncertainty(num_samples=MCQ_SAMPLES),
        ),
        source_data=_mcq_source(),
    )


class LEXamAdapter:
    """Converts LEXam public leaderboard rows into EvaluationLog objects."""

    def fetch_leaderboard(
        self,
        html: str | None = None,
        url: str = LEADERBOARD_URL,
    ) -> list[EvaluationLog]:
        """Fetch the LEXam leaderboard and return one log per model.

        Args:
            html: Optional pre-fetched HTML (used in tests).
            url: Leaderboard HTML URL when *html* is not provided.

        Returns:
            One EvaluationLog per model, combining open and MCQ metrics when
            both are available.
        """
        page_html = html if html is not None else _fetch_html(url)
        open_rows = _extract_section_rows(page_html, OPEN_SECTION_TITLE)
        mcq_rows = _extract_section_rows(page_html, MCQ_SECTION_TITLE)

        open_scores = {row.model_name: row.score for row in open_rows}
        mcq_scores = {row.model_name: row.score for row in mcq_rows}
        model_names = sorted(set(open_scores) | set(mcq_scores))

        retrieved_ts = get_current_unix_timestamp()
        logs: list[EvaluationLog] = []

        for model_name in model_names:
            evaluation_results: list[EvaluationResult] = []
            if model_name in open_scores:
                evaluation_results.append(
                    _build_open_question_result(open_scores[model_name])
                )
            if model_name in mcq_scores:
                evaluation_results.append(
                    _build_mcq_result(mcq_scores[model_name])
                )
            if not evaluation_results:
                continue

            identity = _model_identity(model_name)

            logs.append(
                EvaluationLog(
                    schema_version=SCHEMA_VERSION,
                    # Keyed on the RAW leaderboard label, not the resolved
                    # canonical id: a registry re-mapping must not change this
                    # record's identity.
                    evaluation_id=(
                        f'{BENCHMARK_KEY}/{model_name}/{retrieved_ts}'
                    ),
                    retrieved_timestamp=retrieved_ts,
                    eval_library=EvalLibrary(
                        # The harness, not the benchmark: LEXam documents
                        # evaluation through HuggingFace lighteval community
                        # tasks. No library version is published.
                        name='lighteval',
                        version='unknown',
                        additional_details={
                            'benchmark': BENCHMARK_KEY,
                            'leaderboard_url': LEADERBOARD_PAGE_URL,
                            'github': GITHUB_REPO_URL,
                            'lighteval_tasks': (
                                'community|lexamoq_open_question, '
                                f'community|lexammcq_{MCQ_CONFIG}'
                            ),
                        },
                    ),
                    source_metadata=SourceMetadata(
                        source_name='LEXam Leaderboard',
                        source_type=SourceType.documentation,
                        source_organization_name='LEXam-Benchmark',
                        source_organization_url=GITHUB_REPO_URL,
                        # Relative to the *model developer*: LEXam-Benchmark
                        # scores models it did not build.
                        evaluator_relationship=EvaluatorRelationship.third_party,
                        additional_details={
                            'leaderboard_page': LEADERBOARD_PAGE_URL,
                            'source_html': url,
                            'citation': PAPER_URL,
                        },
                    ),
                    model_info=ModelInfo(
                        name=model_name,
                        id=identity.model_id,
                        developer=identity.developer,
                        additional_details={
                            # LEXam documents both API-served (litellm) and
                            # local vLLM evaluation paths without saying which
                            # produced each leaderboard row.
                            'deployment_type': 'unknown',
                            'model_availability': identity.availability,
                            'model_id_resolution': identity.id_source,
                            'leaderboard_label': model_name,
                        },
                    ),
                    evaluation_results=evaluation_results,
                )
            )

        logger.info('Converted %d LEXam leaderboard model(s).', len(logs))
        return logs
