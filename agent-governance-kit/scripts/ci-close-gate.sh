#!/usr/bin/env bash
set -euo pipefail
python3 tools/agentctl.py close
python3 tools/agentctl.py audit
echo "Close gate + audit passed."
