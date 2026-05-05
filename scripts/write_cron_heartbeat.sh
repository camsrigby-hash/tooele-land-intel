#!/usr/bin/env bash
# write_cron_heartbeat.sh — write a JSON cron-run heartbeat the wasatch-intel
# /api/cron-status endpoint can read via raw.githubusercontent.
#
# Usage: scripts/write_cron_heartbeat.sh <workflow_name> [status] [items_processed] [notes...]
#   workflow_name    — e.g. "weekly-digest", "signals", "gap-layer", "geocode"
#   status           — success | failure | partial   (default: success)
#   items_processed  — integer or empty             (default: empty)
#   notes...         — free-text notes              (default: empty)
#
# Output: data/cron_status/<workflow_name>.json

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 <workflow_name> [status] [items_processed] [notes...]" >&2
  exit 64
fi

NAME="$1"
STATUS="${2:-success}"
ITEMS="${3:-}"
shift 3 2>/dev/null || shift $#
NOTES="$*"

mkdir -p data/cron_status

# JSON-escape notes (just escape backslashes and double-quotes)
ESC_NOTES=$(printf '%s' "$NOTES" | sed 's/\\/\\\\/g; s/"/\\"/g')

cat > "data/cron_status/${NAME}.json" <<EOF
{
  "workflow_name": "${NAME}",
  "ran_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "${STATUS}",
  "items_processed": ${ITEMS:-null},
  "notes": "${ESC_NOTES}"
}
EOF

echo "Wrote data/cron_status/${NAME}.json (status=${STATUS})"
