#!/bin/bash
# Thin wrapper -- delegates to deploy.py for cross-platform parity.
set -e
exec python3 "$(dirname "$0")/deploy.py" "$@"
