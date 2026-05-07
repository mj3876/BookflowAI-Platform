#!/usr/bin/env bash
# Daily 18:00 teardown · helm uninstall + Tier 10-99 stack destroy.
# Tier 00 (foundation) stays · always-on cost ~$16/mo.
#
# base-down internally invokes mocks.destroy() first, so this is a single call.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== 18:00 BOOKFLOW stop-day ==="

py scripts/aws/bookflow.py base-down

echo
echo "=== stop-day complete · only Tier 00 remains ==="
py scripts/aws/bookflow.py status
