#!/usr/bin/env bash
# Validate EEE records (dispatches .json -> aggregate, .jsonl -> instance).
# Usage: scripts/validate.sh <output-dir>
set -euo pipefail
OUT="${1:?usage: validate.sh <output-dir>}"
python -m every_eval_ever validate "$OUT"
