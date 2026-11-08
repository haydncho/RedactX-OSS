#!/usr/bin/env bash
# Explicitly isolated local acceptance; no tunnel, production container or data.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODEL="${REDACTX_VISUAL_MODEL_DIR:-$ROOT/local/visual-acceptance/models}"
for f in model.onnx config.json manifest.json; do
  [ -f "$MODEL/$f" ] || { echo "缺少测试模型：$MODEL/$f" >&2; exit 1; }
done
mkdir -p "$ROOT/local/visual-acceptance/container-data"
container build -t redactx:visual-test -f "$ROOT/deploy/container/VisualTest.Containerfile" "$ROOT"
# No rm/replacement: an existing test container must be stopped explicitly.
container run -d --name redactx-visual-test \
  -p 127.0.0.1:8091:8000 \
  -v "$ROOT/local/visual-acceptance/container-data:/data" \
  --mount "type=bind,source=$MODEL,target=/models/visual,readonly" \
  --read-only --tmpfs /tmp --cap-drop ALL -m 6G -c 6 \
  -e REDACTX_VISUAL_ENABLED=1 -e REDACTX_VISUAL_MODEL_DIR=/models/visual \
  -e REDACTX_VISUAL_THREADS=2 -e REDACTX_PAGE_WORKERS=1 \
  redactx:visual-test
echo "测试服务：http://127.0.0.1:8091/app"
