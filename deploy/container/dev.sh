#!/usr/bin/env bash
# 开发模式：不重建镜像，把仓库里的代码、页面与模型目录挂载进容器，改动即时生效。
# 用法：deploy/container/dev.sh [端口，默认 8090]      重建依赖镜像：deploy/container/dev.sh --rebuild
#
# - Python 代码（redactx/、service/）改动后服务自动重启（约 2 秒；正在处理的任务会中断，需重新提交）；
#   Web 页（web/）直接读挂载的文件，刷新浏览器即可；NER 模型读 models/ner/。
# - 运行环境（字体、LibreOffice、Python 依赖）与正式镜像相同。只有依赖（pyproject.toml / Containerfile）变了才需要 --rebuild。
# - 正式部署仍用 run.sh：按版本号生成镜像，代码打包进镜像。
# 容器名、端口与 run.sh 相同（redactx-oss、8090），两者互相替换。
set -euo pipefail

REBUILD=0
if [ "${1:-}" = "--rebuild" ]; then REBUILD=1; shift; fi
PORT="${1:-${REDACTX_PORT:-8090}}"
NAME="${REDACTX_CONTAINER:-redactx-oss}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")"
DATA="$ROOT/data-container"

mkdir -p "$DATA"
container system status >/dev/null 2>&1 || container system start
if [ ! -f "$ROOT/models/ner/model.onnx" ]; then
  echo "缺少正文人名识别模型：先运行 .venv/bin/python deploy/ner_export.py" >&2
  exit 1
fi

if [ "$REBUILD" = 1 ]; then
  IMAGE="redactx:${VERSION}"
  container build -t "$IMAGE" -f "$ROOT/deploy/container/Containerfile" "$ROOT"
else
  # 用本机已有的最新 redactx 镜像，只取其中的运行环境；代码来自挂载的仓库目录
  IMAGE="$(container image list 2>/dev/null | awk '$1 == "redactx" {print $1 ":" $2}' | sort -V | tail -1)"
  if [ -z "$IMAGE" ]; then
    IMAGE="redactx:${VERSION}"
    container build -t "$IMAGE" -f "$ROOT/deploy/container/Containerfile" "$ROOT"
  fi
fi

# 需要时把 API Key、导出开关传进容器（例如通过隧道对外演示时必须设置 API Key）
EXTRA=()
[ -n "${REDACTX_API_KEY:-}" ] && EXTRA+=(-e "REDACTX_API_KEY=$REDACTX_API_KEY")
[ -n "${REDACTX_ALLOW_EXPORT:-}" ] && EXTRA+=(-e "REDACTX_ALLOW_EXPORT=$REDACTX_ALLOW_EXPORT")

container rm -f "$NAME" >/dev/null 2>&1 || true
container run -d --name "$NAME" \
  -p "127.0.0.1:${PORT}:8000" \
  -v "$DATA:/data" \
  --mount "type=bind,source=$ROOT/redactx,target=/app/redactx,readonly" \
  --mount "type=bind,source=$ROOT/service,target=/app/service,readonly" \
  --mount "type=bind,source=$ROOT/web,target=/app/web,readonly" \
  --mount "type=bind,source=$ROOT/models/ner,target=/app/models/ner,readonly" \
  -e WATCHFILES_FORCE_POLLING=true \
  ${EXTRA[@]+"${EXTRA[@]}"} \
  -m 6G -c 6 \
  "$IMAGE" \
  uvicorn service.app:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/redactx --reload-dir /app/service

echo "锐消 RedactX 开发模式已启动：http://127.0.0.1:${PORT}（运行环境 ${IMAGE}，代码来自 ${ROOT}）"
