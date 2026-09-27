#!/usr/bin/env bash
# 用 Apple container 在本机运行锐消 RedactX（正式部署：代码打包进按版本号生成的镜像）。
# 开发时用同目录的 dev.sh，挂载仓库代码、不重建镜像。
# 用法：deploy/container/run.sh [端口，默认 8090]
# 容器名默认 redactx-oss，可用环境变量 REDACTX_CONTAINER 改；端口也可用 REDACTX_PORT 指定。
# 端口与容器名避开本机其他项目常用的 8080 与 redactx，免得部署时互相顶替。
set -euo pipefail

PORT="${1:-${REDACTX_PORT:-8090}}"
NAME="${REDACTX_CONTAINER:-redactx-oss}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# 镜像标签跟随 pyproject.toml 的版本号，升级后首次运行会自动构建新镜像
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")"
IMAGE="redactx:${VERSION}"
DATA="$ROOT/data-container"

mkdir -p "$DATA"
container system status >/dev/null 2>&1 || container system start

if ! container image inspect "$IMAGE" >/dev/null 2>&1; then
  if [ ! -f "$ROOT/models/ner/model.onnx" ]; then
    echo "缺少正文人名识别模型：先运行 .venv/bin/python deploy/ner_export.py" >&2
    exit 1
  fi
  container build -t "$IMAGE" -f "$ROOT/deploy/container/Containerfile" "$ROOT"
fi

# API Key、导出开关传进容器。通过隧道等方式对外提供服务时必须设置 REDACTX_API_KEY（管理员 Key），
# 再用 deploy/keys.sh 给每位使用者生成用户 Key
EXTRA=()
[ -n "${REDACTX_API_KEY:-}" ] && EXTRA+=(-e "REDACTX_API_KEY=$REDACTX_API_KEY")
[ -n "${REDACTX_ALLOW_EXPORT:-}" ] && EXTRA+=(-e "REDACTX_ALLOW_EXPORT=$REDACTX_ALLOW_EXPORT")
[ -z "${REDACTX_API_KEY:-}" ] && echo "提示：未设置 REDACTX_API_KEY，服务不校验 Key。只在本机使用时可以；对外提供前务必设置" >&2

container rm -f "$NAME" >/dev/null 2>&1 || true
# 只绑定到本机回环地址，其他机器访问不到。根文件系统只读（可写的只有数据卷与 /tmp），去掉全部 Linux 能力
container run -d --name "$NAME" \
  -p "127.0.0.1:${PORT}:8000" \
  -v "$DATA:/data" \
  --read-only --tmpfs /tmp --cap-drop ALL \
  ${EXTRA[@]+"${EXTRA[@]}"} \
  -m 6G -c 6 \
  "$IMAGE"

echo "锐消 RedactX 已启动：http://127.0.0.1:${PORT}"
