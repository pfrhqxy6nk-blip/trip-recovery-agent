#!/usr/bin/env bash
set -euo pipefail

# Reproducible, network-free proof of the autonomous product promise:
# watcher -> sourced disruption -> safe actions -> Telegram approval -> durable resume.
# The tests use deterministic adapters, so this runner never spends Vertex quota or
# mutates a real booking while still exercising the production state boundaries.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing Python runtime: ${PYTHON_BIN}" >&2
  echo "Create the project environment with: python3.12 -m venv .venv" >&2
  exit 2
fi

cd "${ROOT_DIR}"
export PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}"

"${PYTHON_BIN}" -m pytest -q \
  backend/tests/test_first_user_e2e.py \
  backend/tests/test_autonomous_watch_recovery.py \
  backend/tests/test_recovery_e2e.py \
  backend/tests/test_workflow_resume.py \
  backend/tests/test_workflow_commands.py

echo "canonical autonomous E2E: PASS"
