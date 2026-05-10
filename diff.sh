#!/usr/bin/env zsh
# Usage:
#   ./scripts/diff.sh <old_rev> <new_rev> [options]
#
# Examples:
#   ./scripts/diff.sh HEAD~1 HEAD
#   ./scripts/diff.sh HEAD~1 HEAD --only-changed
#   ./scripts/diff.sh HEAD~5 HEAD --context 4
#   ./scripts/diff.sh abc1234 HEAD -o my-report.html

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
DIFF_SCRIPT="$SCRIPT_DIR/thesis_content_diff.py"

if (( $# < 2 )); then
  print -u2 "Usage: $0 <old_rev> <new_rev> [--only-changed] [--context N] [-o output.html]"
  print -u2 ""
  print -u2 "Examples:"
  print -u2 "  $0 HEAD~1 HEAD"
  print -u2 "  $0 HEAD~1 HEAD --only-changed"
  print -u2 "  $0 abc1234 HEAD --context 4 -o report.html"
  exit 1
fi

output=$(uv run "$DIFF_SCRIPT" "$@")
print "Report saved to: $output"

# Open automatically if on macOS
if [[ "$OSTYPE" == darwin* ]]; then
  open "$output"
fi
