"""Keep the eee-dataset-conversion skill honest against the live validator.

No agent runs here. The skill's *prescribed output* is frozen twice over:

* ``.claude/skills/eee-dataset-conversion/templates/`` — the code the skill tells a
  contributor (or an agent) to copy. The tests below execute it.
* ``tests/data/skill_reference_conversion/`` — one committed conversion produced by
  those templates: the artifact a contributor following the skill would submit.

Both are re-checked by the real CLI validator, with the semantic checks that only run
at a canonical ``data/<collection>/<developer>/<model>/`` path. Change the schema, the
validator, or the publisher and this file goes red, naming the skill as the thing to
fix — instead of the change surfacing in a contributor's first PR.

What this does NOT catch: a *new* rule that the reference conversion happens to satisfy
already. Then CI stays green while `reference/datastore-gate.md` quietly goes incomplete.
So when you add a check to the validator, re-derive that file from `REGISTERED_CHECKS`
rather than trusting a green run here.

Regenerate the frozen conversion after a deliberate change:

    python -c "from tests.test_skill_conversion import regenerate_frozen_reference as r; r()"
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import types
import uuid
from pathlib import Path
from unittest import mock

import pytest

from every_eval_ever.helpers import SCHEMA_VERSION
from every_eval_ever.helpers.io import SourceRecordsError
from every_eval_ever.validate import main as validate_main

# --------------------------------------------------------------------------- setup

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / '.claude' / 'skills' / 'eee-dataset-conversion'
TEMPLATE_DIR = SKILL_DIR / 'templates'
FROZEN_DIR = REPO_ROOT / 'tests' / 'data' / 'skill_reference_conversion'

# Pinned so the frozen conversion is byte-reproducible.
FROZEN_UUID = 'f3a1c0de-4b2e-4c1a-9f6d-1b7e5a2c8d40'
FROZEN_EVAL_TS = '1750000000'
FROZEN_RETRIEVED_TS = '1750000001'
SRC_SLUG = 'demo-source'

REGENERATE_CMD = (
    'python -c "from tests.test_skill_conversion import '
    'regenerate_frozen_reference as r; r()"'
)


def _remedy(what_broke: str, *, where: str, regenerate: bool = True) -> str:
    """Build a failure message that names the fix, not just the symptom."""
    lines = [
        f'{what_broke}',
        f'Fix: {where}',
    ]
    if regenerate:
        lines.append(f'Then regenerate the frozen conversion: {REGENERATE_CMD}')
    return '\n'.join(lines)


# ------------------------------------------------------------------ skill templates


def _load_template(name: str) -> types.ModuleType:
    """Import a skill template by path (they live outside the package tree).

    The templates ship `<src>` placeholders on purpose — the datastore path helpers
    reject them, so a half-filled-in copy fails loudly. Substituting a real slug is
    the first edit a contributor makes, so the test makes it too.
    """
    path = TEMPLATE_DIR / name
    if not path.is_file():
        pytest.skip(f'skill template not present: {path}')
    spec = importlib.util.spec_from_file_location(f'_skill_{path.stem}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.SRC == '<src>', _remedy(
        f'{name} no longer uses the `<src>` placeholder this test substitutes.',
        where=f'keep the placeholder in {name}, or update SRC_SLUG wiring here',
        regenerate=False,
    )
    module.SRC = SRC_SLUG
    module.COLLECTION = SRC_SLUG
    return module


# ------------------------------------------------------------------- source fixtures


def _valid_row() -> dict:
    """One convertible source row, in the dict shape the template documents."""
    return {
        'model': 'demo-model',
        'developer': 'demo-org',
        'eval_ts': FROZEN_EVAL_TS,
        'results': [
            {
                'benchmark': 'demo_bench',
                'score': 0.75,
                'dataset_url': 'https://example.invalid/demo_bench',
            }
        ],
    }


def _unconvertible_row() -> dict:
    """A row the template must report as a failure rather than skip."""
    return {'model': 'broken-model'}  # no developer / results


def _sample_item() -> types.SimpleNamespace:
    """One per-item record, shaped as the sidecar template consumes it."""
    return types.SimpleNamespace(
        benchmark='demo_bench',
        sample_id='demo_0001',
        raw='What is 2 + 2?',
        reference=['4'],
        prompt='Q: What is 2 + 2?\nA:',
        output=['The answer is 4.'],
        parsed='4',
        scorer='match',
        score=1.0,
        is_correct=True,
        in_tok=12,
        out_tok=5,
        subject='arithmetic',
    )


# ------------------------------------------------------------------------- the gate


def _assert_gate_clean(paths: list[Path], capsys) -> None:
    """Run the real validator CLI over `paths` and require errors AND warnings empty.

    Warnings count as drift: the datastore bot reports them and reviewers ask about
    them, so records built the way the skill prescribes must produce none.
    """
    exit_code = validate_main([str(path) for path in paths] + ['--format', 'json'])
    reports = json.loads(capsys.readouterr().out)
    complaints = [
        {'file': report['file'], 'errors': report['errors'], 'warnings': report['warnings']}
        for report in reports
        if not report['valid'] or report['errors'] or report['warnings']
    ]
    assert exit_code == 0 and not complaints, _remedy(
        "The skill's own output no longer passes the merge gate:\n"
        + json.dumps(complaints, indent=2),
        where=(
            'update the rule in .claude/skills/eee-dataset-conversion/reference/'
            'datastore-gate.md and whichever templates/ file emits the field'
        ),
    )


# ----------------------------------------------------------------------------- tests


def test_skill_records_the_current_schema_version():
    """A schema bump must force a re-read of the skill's field claims."""
    skill_text = (SKILL_DIR / 'SKILL.md').read_text(encoding='utf-8')
    recorded = re.search(r'`SCHEMA_VERSION`\s+`([0-9]+\.[0-9]+\.[0-9]+)`', skill_text)
    assert recorded, _remedy(
        'SKILL.md no longer records the schema version it was written against.',
        where='restore the "Written against EEE `SCHEMA_VERSION` `x.y.z`" line',
        regenerate=False,
    )
    assert recorded.group(1) == SCHEMA_VERSION, _remedy(
        f'The skill is written against schema {recorded.group(1)} but the library is '
        f'at {SCHEMA_VERSION}.',
        where=(
            're-verify .claude/skills/eee-dataset-conversion/reference/*.md against '
            'the new schema, then bump the marker in SKILL.md'
        ),
    )


def test_aggregate_template_publishes_gate_clean_records(tmp_path, capsys):
    """The aggregate template alone produces a submittable record."""
    aggregate = _load_template('aggregate_adapter.py')
    out_root = tmp_path / 'data'

    result = aggregate.convert_rows([_valid_row()], out_root, FROZEN_RETRIEVED_TS)
    paths = aggregate.save_evaluation_logs(result.records)

    assert [path.parent for path in paths] == [
        out_root / SRC_SLUG / 'demo-org' / 'demo-model'
    ], _remedy(
        f'The template published to {[str(p) for p in paths]}.',
        where='the collection/developer/model routing in templates/aggregate_adapter.py',
    )
    _assert_gate_clean(paths, capsys)


def test_aggregate_template_accounts_for_unconvertible_rows(tmp_path):
    """A row that cannot be converted is reported and fails the command."""
    aggregate = _load_template('aggregate_adapter.py')
    out_root = tmp_path / 'data'

    result = aggregate.convert_rows(
        [_valid_row(), _unconvertible_row()], out_root, FROZEN_RETRIEVED_TS
    )

    assert (result.total_records, len(result.records), len(result.failures)) == (2, 1, 1), _remedy(
        'The template silently dropped or silently kept an unconvertible row.',
        where=(
            'the SourceRecordFailure branch in templates/aggregate_adapter.py '
            '(see reference/datastore-gate.md §partial conversions)'
        ),
    )

    report_path = aggregate.save_failure_report(
        result, aggregate.default_failure_report_path(out_root / SRC_SLUG)
    )
    assert out_root not in report_path.parents, _remedy(
        f'The failure report landed inside the validated data tree at {report_path}.',
        where='use default_failure_report_path so reports go to adapter_reports/',
    )
    json.loads(report_path.read_text(encoding='utf-8'))  # strict JSON

    with pytest.raises(SourceRecordsError):
        result.raise_if_incomplete()


def test_instance_template_publishes_gate_clean_pair(tmp_path, capsys):
    """Aggregate + sidecar publish together, sharing one uuid and folder."""
    aggregate = _load_template('aggregate_adapter.py')
    sidecar = _load_template('instance_sidecar.py')

    log = aggregate.make_log(_valid_row(), FROZEN_RETRIEVED_TS)
    paths = sidecar.export_with_instances(
        log,
        'demo-org',
        'demo-model',
        [_sample_item()],
        tmp_path / 'data',
        tmp_path / 'staged' / 'data',
        collection=SRC_SLUG,
    )

    published = sorted(paths[0].parent.iterdir())
    assert [path.suffix for path in published] == ['.json', '.jsonl'], _remedy(
        f'Expected one aggregate + one sidecar, got {[p.name for p in published]}.',
        where='the staging/publish sequence in templates/instance_sidecar.py',
    )
    _assert_gate_clean(published, capsys)


def test_frozen_reference_conversion_still_passes_the_gate(capsys):
    """The committed conversion a contributor would submit, re-checked as-is."""
    files = _frozen_record_files()
    if not files:
        pytest.skip(f'no frozen reference conversion under {FROZEN_DIR}')
    _assert_gate_clean(files, capsys)


def test_frozen_reference_conversion_matches_the_templates(tmp_path):
    """The frozen conversion must still be what the templates produce today.

    Without this, editing a template and forgetting to regenerate would leave a stale
    fixture that keeps passing — and the two frozen artifacts would drift apart.
    """
    if not _frozen_record_files():
        pytest.skip(f'no frozen reference conversion under {FROZEN_DIR}')
    regenerate_frozen_reference(tmp_path / 'regenerated')

    committed = {
        path.relative_to(FROZEN_DIR): path.read_bytes()
        for path in _frozen_record_files()
    }
    rebuilt = {
        path.relative_to(tmp_path / 'regenerated'): path.read_bytes()
        for path in _frozen_record_files(tmp_path / 'regenerated')
    }
    assert committed == rebuilt, _remedy(
        'The templates no longer produce the committed frozen conversion.',
        where=(
            'if the template change was intentional, nothing — just regenerate; '
            'otherwise reconcile templates/ with reference/'
        ),
    )


# ---------------------------------------------------------------------- regeneration

FROZEN_README = f"""Frozen output of the `eee-dataset-conversion` skill templates — the
artifact a contributor following the skill would submit to the EEE_datastore.

Re-validated as-is by tests/test_skill_conversion.py using the CLI semantic checks,
and compared byte-for-byte against what the templates produce today.

Do not hand-edit. Regenerate with:

    {REGENERATE_CMD}
"""


def _frozen_record_files(root: Path | None = None) -> list[Path]:
    """Every record file in a frozen conversion tree, in a stable order."""
    root = FROZEN_DIR if root is None else root
    return sorted(
        path
        for path in root.rglob('*')
        if path.suffix in {'.json', '.jsonl'} and path.is_file()
    )


def regenerate_frozen_reference(dest: Path | None = None) -> list[Path]:
    """Rewrite a frozen conversion tree from the skill's templates."""
    dest = FROZEN_DIR if dest is None else Path(dest)
    aggregate = _load_template('aggregate_adapter.py')
    sidecar = _load_template('instance_sidecar.py')

    if dest.exists():
        shutil.rmtree(dest)
    staged = dest / '_staged' / 'data'

    log = aggregate.make_log(_valid_row(), FROZEN_RETRIEVED_TS)
    with mock.patch.object(uuid, 'uuid4', return_value=uuid.UUID(FROZEN_UUID)):
        paths = sidecar.export_with_instances(
            log,
            'demo-org',
            'demo-model',
            [_sample_item()],
            dest / 'data',
            staged,
            collection=SRC_SLUG,
        )
    shutil.rmtree(dest / '_staged')
    (dest / 'README.md').write_text(FROZEN_README, encoding='utf-8')
    return paths
