#!/usr/bin/env bash
# The field test in the real game on a machine without a screen: a virtual display, the stand-in AI, the brain's
# session (the commander's game opens a new world, Altron's body joins it), then brain/field_test.py.
#   ci/run_field_test.sh [parts, default "hands"]
set -u
ONLY="${1:-hands}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/test-reports"
export DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1
Xvfb :99 -screen 0 1280x1024x24 > "$ROOT/test-reports/xvfb.log" 2>&1 &
PORT=$(python -c "import json; print(json.load(open('$ROOT/brain/config.json'))['llm_port'])")
python "$ROOT/ci/fake_llm.py" "$PORT" &
sleep 2
cd "$ROOT/brain"
python -u session.py CIWorld > "$ROOT/test-reports/session.log" 2>&1 &
SESSION=$!
python -u field_test.py --only "$ONLY" 2>&1 | tee "$ROOT/test-reports/field_test_console.log"
STATUS=${PIPESTATUS[0]}
kill "$SESSION" 2>/dev/null
sleep 5
pkill -f java 2>/dev/null
REPORT=$(ls -t "$ROOT"/test-reports/field_test_*.md 2>/dev/null | head -1)
if [ -n "$REPORT" ]; then
  [ -n "${GITHUB_STEP_SUMMARY:-}" ] && cat "$REPORT" >> "$GITHUB_STEP_SUMMARY"
  if grep -q "❌" "$REPORT"; then echo "Field test: some steps failed (see the report)"; exit 1; fi
else
  echo "No report: the session did not come up (see test-reports/session.log)"; tail -80 "$ROOT/test-reports/session.log"; exit 1
fi
exit "$STATUS"
