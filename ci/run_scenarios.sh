#!/usr/bin/env bash
# One shard of Altron's scenarios on a GitHub machine: the AI server (llama.cpp on the processor, the commander's Qwen
# 9B model, the same flags the brain uses), then brain/scenarios.py against the simulated world.
#   ci/run_scenarios.sh <shard i/n> <minutes> [only]
set -u
SHARD="$1"; MINUTES="$2"; ONLY="${3:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/test-reports/scenarios"
mkdir -p "$OUT"
LD_LIBRARY_PATH="$(dirname "$SRV")" "$SRV" -m "$ROOT/models/Qwen3.5-9B-Q4_K_M.gguf" -c 24576 --host 127.0.0.1 --port 8081 \
  --jinja -np 1 -cram 1024 -t "$(nproc)" > "$OUT/llama-server-${SHARD/\//of}.log" 2>&1 &
for i in $(seq 1 300); do curl -sf http://127.0.0.1:8081/health > /dev/null && break; sleep 2; done
cd "$ROOT/brain"
ARGS=(--shard "$SHARD" --minutes "$MINUTES" --out "$OUT" --knowledge)
[ -n "$ONLY" ] && ARGS+=(--only "$ONLY")
ALTRON_LLM_PORT=8081 python -u scenarios.py "${ARGS[@]}"
