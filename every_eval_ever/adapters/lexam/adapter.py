"""Adapter for converting LEXam public leaderboard HTML to every_eval_ever format.

Scope of the published leaderboard (https://lexam-benchmark.github.io/):

* "Judge Scores on Open Questions" — mean judge score over the
  `open_question` **test** split (n=2,541), scored by a pointwise-minimum
  ensemble of GPT-4o, DeepSeek-V3 and Qwen3-32B.
* "Accuracy on Multiple-Choice Questions" — accuracy on `mcq_4_choices`
  (n=1,655) only. It is *not* pooled over the 8/16/32-choice configs.

Model identity comes from the eval-card-registry; every record reports how it
was resolved in `model_info.additional_details.model_id_resolution`.

`DeepSeek-V3.2-chat` and `DeepSeek-V3.2-reasoner` are not two checkpoints:
per DeepSeek's API changelog (2025-12-01) `deepseek-chat` and
`deepseek-reasoner` are the non-thinking and thinking modes of the same
`DeepSeek-V3.2` release, which matches the paper listing `-reasoner` under
reasoning models and `-chat` under large (non-reasoning) models. Both rows
therefore share `model_info.id` and are distinguished by
`generation_config.generation_args.reasoning`.

Standard errors are not on the leaderboard page; they come from the paper's
tables and are attached only while the scraped score still equals the score
the paper reports (see `_score_details`).
"""

from __future__ import annotations

import argparse
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

from every_eval_ever.converters import SCHEMA_VERSION
from every_eval_ever.converters.common.publication import (
    publish_evaluation_logs,
)
from every_eval_ever.converters.common.utils import get_current_unix_timestamp
from every_eval_ever.eval_types import (
    EvalLibrary,
    EvaluationLog,
    EvaluationResult,
    EvaluatorRelationship,
    GenerationArgs,
    GenerationConfig,
    JudgeConfig,
    LlmScoring,
    MetricConfig,
    ModelInfo,
    ScoreDetails,
    ScoreType,
    SourceDataHf,
    SourceMetadata,
    SourceType,
    StandardError,
    Uncertainty,
)
from every_eval_ever.helpers.io import (
    SourceConversionResult,
    SourceRecordFailure,
    default_failure_report_path,
    save_failure_report,
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
PAPER_TABLE_CITATION = (
    'arXiv:2505.12864v7 (ICLR 2026), Table 1 (open questions) and Table 10 '
    '(MCQ-4), bootstrapped standard error'
)
DEEPSEEK_MODE_CITATION = (
    'https://api-docs.deepseek.com/updates (2025-12-01): deepseek-chat and '
    'deepseek-reasoner are the non-thinking and thinking modes of '
    'DeepSeek-V3.2'
)
BENCHMARK_KEY = 'lexam'
DEFAULT_OUTPUT_DIR = 'data'

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
    """Identity for one LEXam leaderboard label.

    `model_id` is the join key written to `model_info.id`. The
    eval-card-registry is the authority; `id_source` records how the id was
    obtained so a consumer can tell a registry-backed id from a gap-filling
    one:

    * `registry_alias` — a confirmed registry alias maps this leaderboard
      label to this canonical id.
    * `registry_canonical` — the label matches an existing reviewed canonical
      one-to-one, but the alias is not registered yet.
    * `hf_canonical` — the registry has no canonical for the evaluated
      checkpoint (only an API-catalog draft, a base model, or nothing), so the
      canonical Hugging Face repo id is used. `registry_canonical_id` carries
      the draft the registry currently returns, when one exists.

    `developer_org_id` is the registry's normalized company org, which differs
    from the id prefix whenever the id is a Hugging Face repo id (for example
    `Qwen/Qwen3-32B` under org `alibaba`). It is recorded in
    `model_info.additional_details` because the datastore path is derived from
    the id prefix, not from this field.
    """

    model_id: str
    developer_org_id: str
    availability: str
    id_source: str
    registry_canonical_id: str | None = None
    reasoning: bool | None = None
    api_model_name: str | None = None
    note: str | None = None

    @property
    def developer(self) -> str:
        """Developer as it appears in the id, matching the datastore path."""
        return self.model_id.split('/')[0]


_MODEL_IDENTITIES = {
    'Apertus-70B': ModelIdentity(
        model_id='swiss-ai-initiative/apertus-70b',
        developer_org_id='swiss-ai-initiative',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Apertus-8B': ModelIdentity(
        model_id='swiss-ai-initiative/apertus-8b',
        developer_org_id='swiss-ai-initiative',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Claude-3.7-Sonnet': ModelIdentity(
        model_id='anthropic/Claude-3.7-Sonnet',
        developer_org_id='anthropic',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'Claude-4.5-Sonnet': ModelIdentity(
        model_id='anthropic/claude-sonnet-4.5',
        developer_org_id='anthropic',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'DeepSeek-R1': ModelIdentity(
        model_id='deepseek-ai/DeepSeek-R1',
        developer_org_id='deepseek',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'DeepSeek-V3': ModelIdentity(
        model_id='deepseek-ai/DeepSeek-V3',
        developer_org_id='deepseek',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'DeepSeek-V3.2-Exp': ModelIdentity(
        model_id='deepseek-ai/DeepSeek-V3.2-Exp',
        developer_org_id='deepseek',
        availability='open_weights',
        id_source='hf_canonical',
        registry_canonical_id='deepseek/deepseek-v3-2-exp',
        note='registry has only an API-catalog draft for this checkpoint',
    ),
    'DeepSeek-V3.2-chat': ModelIdentity(
        model_id='deepseek-ai/DeepSeek-V3.2',
        developer_org_id='deepseek',
        availability='open_weights',
        id_source='registry_canonical',
        reasoning=False,
        api_model_name='deepseek-chat',
        note='deepseek-chat API endpoint = V3.2 non-thinking mode',
    ),
    'DeepSeek-V3.2-reasoner': ModelIdentity(
        model_id='deepseek-ai/DeepSeek-V3.2',
        developer_org_id='deepseek',
        availability='open_weights',
        id_source='registry_canonical',
        reasoning=True,
        api_model_name='deepseek-reasoner',
        note='deepseek-reasoner API endpoint = V3.2 thinking mode',
    ),
    'EuroLLM-9B-it': ModelIdentity(
        model_id='utter-project/EuroLLM-9B-Instruct',
        developer_org_id='utter-project',
        availability='open_weights',
        id_source='hf_canonical',
        note='registry holds only the base EuroLLM-9B',
    ),
    'GPT-4.1': ModelIdentity(
        model_id='openai/gpt-4.1',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-4.1-mini': ModelIdentity(
        model_id='openai/gpt-4.1-mini',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-4.1-nano': ModelIdentity(
        model_id='openai/gpt-4.1-nano',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-4o': ModelIdentity(
        model_id='openai/gpt-4o',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-4o-mini': ModelIdentity(
        model_id='openai/gpt-4o-mini',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-5': ModelIdentity(
        model_id='openai/gpt-5',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-5-mini': ModelIdentity(
        model_id='openai/gpt-5-mini',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-5-nano': ModelIdentity(
        model_id='openai/gpt-5-nano',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'GPT-OSS-120B': ModelIdentity(
        model_id='openai/gpt-oss-120b',
        developer_org_id='openai',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'GPT-OSS-20B': ModelIdentity(
        model_id='openai/gpt-oss-20b',
        developer_org_id='openai',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Gemini-2.5-Pro': ModelIdentity(
        model_id='google/gemini-2.5-pro',
        developer_org_id='google',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'Gemini-3-Pro-preview': ModelIdentity(
        model_id='google/gemini-3-pro-preview',
        developer_org_id='google',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'Gemma-2-9B-it': ModelIdentity(
        model_id='google/gemma-2-9b-it',
        developer_org_id='google',
        availability='open_weights',
        id_source='registry_canonical',
        note='label is the instruct variant; the alias resolves to the base model',
    ),
    'Gemma-3-12B-it': ModelIdentity(
        model_id='google/gemma-3-12b-it',
        developer_org_id='google',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Llama-3.1-405B-it': ModelIdentity(
        model_id='meta-llama/Llama-3.1-405B-Instruct',
        developer_org_id='meta',
        availability='open_weights',
        id_source='hf_canonical',
        note='registry holds only Together "Turbo" drafts',
    ),
    'Llama-3.1-8B-it': ModelIdentity(
        model_id='meta-llama/Llama-3.1-8B-Instruct',
        developer_org_id='meta',
        availability='open_weights',
        id_source='registry_canonical',
    ),
    'Llama-3.3-70B-it': ModelIdentity(
        model_id='meta-llama/Llama-3.3-70B-Instruct',
        developer_org_id='meta',
        availability='open_weights',
        id_source='registry_canonical',
    ),
    'Llama-4-Maverick': ModelIdentity(
        model_id='meta-llama/Llama-4-Maverick-17B-128E',
        developer_org_id='meta',
        availability='open_weights',
        id_source='registry_alias',
        note='leaderboard label does not state the Instruct/FP8 variant',
    ),
    'Ministral-8B-it': ModelIdentity(
        model_id='mistralai/ministral-8b-it',
        developer_org_id='mistralai',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'O3-mini': ModelIdentity(
        model_id='openai/o3-mini',
        developer_org_id='openai',
        availability='closed_weights',
        id_source='registry_alias',
    ),
    'Phi-4': ModelIdentity(
        model_id='microsoft/phi-4',
        developer_org_id='microsoft',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'QwQ-32B': ModelIdentity(
        model_id='Qwen/QwQ-32B',
        developer_org_id='alibaba',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Qwen-2.5-7B-it': ModelIdentity(
        model_id='Qwen/Qwen2.5-7B-Instruct',
        developer_org_id='alibaba',
        availability='open_weights',
        id_source='registry_canonical',
    ),
    'Qwen3-235B': ModelIdentity(
        model_id='Qwen/Qwen3-235B-A22B',
        developer_org_id='alibaba',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Qwen3-32B': ModelIdentity(
        model_id='Qwen/Qwen3-32B',
        developer_org_id='alibaba',
        availability='open_weights',
        id_source='registry_alias',
    ),
    'Qwen3-Next': ModelIdentity(
        model_id='alibaba/qwen3-next',
        developer_org_id='alibaba',
        availability='open_weights',
        id_source='registry_alias',
        note='registry id is variant-agnostic; HF publishes Instruct and Thinking variants',
    ),
}

_PAPER_UNCERTAINTY = {
    'Apertus-70B': {'open': (34.7, 0.39)},
    'Apertus-8B': {'open': (22.44, 0.41)},
    'Claude-3.7-Sonnet': {'open': (62.86, 0.51), 'mcq': (57.23, 1.21)},
    'Claude-4.5-Sonnet': {'open': (62.76, 0.43), 'mcq': (58.01, 1.17)},
    'DeepSeek-R1': {'open': (55.91, 0.51), 'mcq': (52.41, 1.22)},
    'DeepSeek-V3': {'open': (52.53, 0.48), 'mcq': (46.57, 1.28)},
    'DeepSeek-V3.2-Exp': {'open': (57.42, 0.45), 'mcq': (53.07, 1.22)},
    'DeepSeek-V3.2-chat': {'open': (55.99, 0.45)},
    'DeepSeek-V3.2-reasoner': {'open': (56.53, 0.45)},
    'EuroLLM-9B-it': {'open': (22.95, 0.35)},
    'GPT-4.1': {'open': (57.5, 0.51), 'mcq': (54.4, 1.26)},
    'GPT-4.1-mini': {'open': (54.58, 0.43), 'mcq': (48.49, 1.22)},
    'GPT-4.1-nano': {'open': (43.68, 0.41), 'mcq': (39.22, 1.22)},
    'GPT-4o': {'open': (56.93, 0.48), 'mcq': (53.13, 1.2)},
    'GPT-4o-mini': {'open': (42.55, 0.39), 'mcq': (40.96, 1.21)},
    'GPT-5': {'open': (70.2, 0.41), 'mcq': (62.65, 1.17)},
    'GPT-5-mini': {'open': (60.32, 0.45), 'mcq': (54.82, 1.19)},
    'GPT-5-nano': {'open': (27.25, 0.63), 'mcq': (47.11, 1.19)},
    'GPT-OSS-120B': {'open': (51.74, 0.46), 'mcq': (47.71, 1.21)},
    'GPT-OSS-20B': {'open': (32.12, 0.37), 'mcq': (40.78, 1.23)},
    'Gemini-2.5-Pro': {'open': (67.4, 0.51), 'mcq': (55.72, 1.18)},
    'Gemini-3-Pro-preview': {'open': (55.38, 0.64)},
    'Gemma-2-9B-it': {'open': (27.41, 0.37), 'mcq': (25.36, 1.04)},
    'Gemma-3-12B-it': {'open': (41.29, 0.48), 'mcq': (29.94, 1.1)},
    'Llama-3.1-405B-it': {'open': (43.14, 0.41), 'mcq': (43.19, 1.19)},
    'Llama-3.1-8B-it': {'open': (10.0, 0.26), 'mcq': (24.04, 1.05)},
    'Llama-3.3-70B-it': {'open': (41.27, 0.41), 'mcq': (28.19, 1.1)},
    'Llama-4-Maverick': {'open': (47.25, 0.46), 'mcq': (49.1, 1.24)},
    'Ministral-8B-it': {'open': (14.88, 0.32), 'mcq': (26.27, 1.12)},
    'O3-mini': {'open': (48.13, 0.49), 'mcq': (44.22, 1.23)},
    'Phi-4': {'open': (38.54, 0.42), 'mcq': (40.66, 1.19)},
    'QwQ-32B': {'open': (44.36, 0.53), 'mcq': (47.83, 1.23)},
    'Qwen-2.5-7B-it': {'open': (16.67, 0.29), 'mcq': (29.28, 1.1)},
    'Qwen3-235B': {'open': (47.25, 0.46), 'mcq': (48.19, 1.2)},
    'Qwen3-32B': {'open': (40.0, 0.43), 'mcq': (45.3, 1.23)},
    'Qwen3-Next': {'open': (43.37, 0.48), 'mcq': (43.31, 1.21)},
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


def _score_details(score: float, label: str, section: str) -> ScoreDetails:
    """Score plus the paper's bootstrapped standard error when it still applies.

    The leaderboard HTML publishes no uncertainty, so the standard error comes
    from the paper's Table 1 / Table 10. It is attached only when the scraped
    score still equals the score the paper reports for that model, so a
    leaderboard update drops the standard error instead of pairing it with a
    number it was never computed for.
    """
    score = round(score, 2)
    samples = (
        OPEN_QUESTIONS_SAMPLES if section == 'open' else MCQ_SAMPLES
    )
    published = _PAPER_UNCERTAINTY.get(label, {}).get(section)
    if published is None or published[0] != score:
        return ScoreDetails(
            score=score,
            uncertainty=Uncertainty(num_samples=samples),
        )
    return ScoreDetails(
        score=score,
        uncertainty=Uncertainty(
            standard_error=StandardError(
                value=published[1], method='bootstrap'
            ),
            num_samples=samples,
        ),
        details={'standard_error_source': PAPER_TABLE_CITATION},
    )


def _build_open_question_result(
    score: float, label: str, identity: ModelIdentity
) -> EvaluationResult:
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
        score_details=_score_details(score, label, 'open'),
        source_data=_open_question_source(),
        generation_config=_generation_config(identity),
    )


def _build_mcq_result(
    score: float, label: str, identity: ModelIdentity
) -> EvaluationResult:
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
        score_details=_score_details(score, label, 'mcq'),
        source_data=_mcq_source(),
        generation_config=_generation_config(identity),
    )


def _model_details(identity: ModelIdentity, label: str) -> dict[str, str]:
    """Model provenance, including how the id was resolved."""
    details = {
        # LEXam documents both API-served (litellm) and local vLLM evaluation
        # paths without stating which produced each leaderboard row.
        'deployment_type': 'unknown',
        'model_availability': identity.availability,
        'model_id_resolution': identity.id_source,
        'developer_org_id': identity.developer_org_id,
        'leaderboard_label': label,
    }
    if identity.registry_canonical_id is not None:
        details['registry_canonical_id'] = identity.registry_canonical_id
    if identity.api_model_name is not None:
        details['api_model_name'] = identity.api_model_name
        details['api_mode_source'] = DEEPSEEK_MODE_CITATION
    if identity.note is not None:
        details['model_id_note'] = identity.note
    return details


def _generation_config(identity: ModelIdentity) -> GenerationConfig | None:
    """Only set what the source states: the two DeepSeek API modes."""
    if identity.reasoning is None:
        return None
    return GenerationConfig(
        generation_args=GenerationArgs(reasoning=identity.reasoning),
        additional_details={
            'reasoning_source': DEEPSEEK_MODE_CITATION,
        },
    )


class LEXamAdapter:
    """Converts LEXam public leaderboard rows into EvaluationLog objects."""

    def fetch_leaderboard(
        self,
        html: str | None = None,
        url: str = LEADERBOARD_URL,
        only: str | None = None,
    ) -> list[EvaluationLog]:
        """Fetch the LEXam leaderboard and return one log per model.

        Args:
            html: Optional pre-fetched HTML (used in tests).
            url: Leaderboard HTML URL when *html* is not provided.
            only: Convert just this leaderboard label, when given.

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
        if only is not None:
            model_names = [name for name in model_names if name == only]

        retrieved_ts = get_current_unix_timestamp()
        logs: list[EvaluationLog] = []

        for model_name in model_names:
            identity = _model_identity(model_name)
            evaluation_results: list[EvaluationResult] = []
            if model_name in open_scores:
                evaluation_results.append(
                    _build_open_question_result(
                        open_scores[model_name], model_name, identity
                    )
                )
            if model_name in mcq_scores:
                evaluation_results.append(
                    _build_mcq_result(
                        mcq_scores[model_name], model_name, identity
                    )
                )
            if not evaluation_results:
                continue

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
                        additional_details=_model_details(
                            identity, model_name
                        ),
                    ),
                    evaluation_results=evaluation_results,
                )
            )

        logger.info('Converted %d LEXam leaderboard model(s).', len(logs))
        return logs

    def fetch_leaderboard_result(
        self,
        html: str | None = None,
        url: str = LEADERBOARD_URL,
    ) -> SourceConversionResult[EvaluationLog]:
        """Convert the leaderboard, recording unmapped rows as failures.

        A leaderboard label with no identity mapping is reported rather than
        aborting the run, so the remaining models are still written and the
        gap shows up in the provenance report. `raise_if_incomplete` still
        fails the command afterwards.
        """
        page_html = html if html is not None else _fetch_html(url)
        open_rows = _extract_section_rows(page_html, OPEN_SECTION_TITLE)
        mcq_rows = _extract_section_rows(page_html, MCQ_SECTION_TITLE)
        labels = sorted(
            {row.model_name for row in open_rows}
            | {row.model_name for row in mcq_rows}
        )

        records: list[EvaluationLog] = []
        failures: list[SourceRecordFailure] = []
        for label in labels:
            try:
                records.extend(
                    self.fetch_leaderboard(html=page_html, url=url, only=label)
                )
            except ValueError as exc:
                failures.append(
                    SourceRecordFailure(
                        source_ref=f'LEXam leaderboard row {label!r}',
                        reason=str(exc),
                        source_record={'model_name': label},
                    )
                )

        return SourceConversionResult(
            source_name='LEXam Leaderboard',
            total_records=len(labels),
            records=records,
            failures=failures,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Convert the LEXam public leaderboard to Every Eval Ever records.'
        ),
    )
    parser.add_argument(
        '--input-html',
        type=Path,
        help=(
            'Read leaderboard HTML from a file instead of fetching it live '
            '(useful for offline smoke runs).'
        ),
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path(DEFAULT_OUTPUT_DIR),
        help=f'Output directory (default: {DEFAULT_OUTPUT_DIR}).',
    )
    parser.add_argument(
        '--source-url',
        default=LEADERBOARD_URL,
        help=f'Leaderboard HTML URL (default: {LEADERBOARD_URL}).',
    )
    parser.add_argument(
        '--failure-report',
        type=Path,
        help='Where to write the provenance report when rows are rejected.',
    )
    return parser.parse_args(argv)


def export(
    logs: list[EvaluationLog], output_dir: Path | str
) -> list[Path]:
    return publish_evaluation_logs(
        logs, output_dir, [str(uuid.uuid4()) for _ in logs]
    )


def run(args: argparse.Namespace) -> int:
    html = (
        args.input_html.read_text(encoding='utf-8')
        if args.input_html is not None
        else None
    )
    result = LEXamAdapter().fetch_leaderboard_result(
        html=html, url=args.source_url
    )
    paths = export(result.records, args.output_dir)
    for path in paths:
        print(path)
    if result.failures:
        report_path = save_failure_report(
            result,
            args.failure_report
            or default_failure_report_path(args.output_dir),
        )
        print(f'Failure report: {report_path}')
        result.raise_if_incomplete()
    return len(paths)


if __name__ == '__main__':
    written = run(parse_args())
    print(f'Wrote {written} LEXam model log(s).')
