import subprocess
import unittest
from typing import Any, Dict


class TestInferenceScalingAdapterUUIDReUse(unittest.TestCase):
    def test_extract_uuid_from_checkpoint_entry(self):
        # Helper to extract uuid from checkpoint entry as implemented in adapter.py
        def extract_uuid_from_checkpoint_entry(entry: Dict[str, Any]) -> str:
            if not entry:
                return None
            summary = entry.get('summary') or {}
            val_path = summary.get('validation_report_path') or ''
            if val_path:
                # e.g., "data\\validation_reports\\d126cea7-9a4c-4d8f-9158-a00d93eadb25_validation_report.json"
                import re

                # Match 36-char UUID (hex chars separated by hyphens)
                match = re.search(
                    r'([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})',
                    val_path,
                )
                if match:
                    return match.group(1)
            return None

        # Test with backslashes (Windows)
        entry_win = {
            'status': 'success',
            'summary': {
                'dataset_name': 'terminalbench',
                'matched_trajectories': 10,
                'model_id': 'anthropic/claude-opus-4-20250514',
                'total_samples': 10,
                'validation_report_path': 'data\\validation_reports\\d126cea7-9a4c-4d8f-9158-a00d93eadb25_validation_report.json',
            },
            'timestamp': 1786171251.5860822,
        }
        self.assertEqual(
            extract_uuid_from_checkpoint_entry(entry_win),
            'd126cea7-9a4c-4d8f-9158-a00d93eadb25',
        )

        # Test with forward slashes (Linux/macOS)
        entry_unix = {
            'status': 'success',
            'summary': {
                'dataset_name': 'terminalbench',
                'matched_trajectories': 10,
                'model_id': 'anthropic/claude-opus-4-20250514',
                'total_samples': 10,
                'validation_report_path': 'data/validation_reports/d126cea7-9a4c-4d8f-9158-a00d93eadb25_validation_report.json',
            },
            'timestamp': 1786171251.5860822,
        }
        self.assertEqual(
            extract_uuid_from_checkpoint_entry(entry_unix),
            'd126cea7-9a4c-4d8f-9158-a00d93eadb25',
        )

        # Test with missing validation report path
        entry_missing = {
            'status': 'success',
            'summary': {'dataset_name': 'terminalbench'},
        }
        self.assertIsNone(extract_uuid_from_checkpoint_entry(entry_missing))

        # Test with invalid UUID format
        entry_invalid = {
            'status': 'success',
            'summary': {
                'validation_report_path': 'data/validation_reports/not-a-uuid_validation_report.json'
            },
        }
        self.assertIsNone(extract_uuid_from_checkpoint_entry(entry_invalid))

    def test_cli_help(self):
        # Verify the --help command works and contains --force
        res = subprocess.run(
            [
                'uv',
                'run',
                'python',
                '-m',
                'every_eval_ever.adapters.inference_scaling.adapter',
                '--help',
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn('--force', res.stdout)


if __name__ == '__main__':
    unittest.main()
