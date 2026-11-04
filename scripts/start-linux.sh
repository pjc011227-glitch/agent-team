#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -e .
python3 -m agent_team.server
