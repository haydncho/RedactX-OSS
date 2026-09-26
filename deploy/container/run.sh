#!/usr/bin/env bash
# 用 Apple container 在本机运行锐消 RedactX。
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
  container build -t "$IMAGE" -f "$ROOT/deploy/container/Containerfile" "$ROOT"
fi

container rm -f "$NAME" >/dev/null 2>&1 || true
# 只绑定到本机回环地址，其他机器访问不到
container run -d --name "$NAME" \
  -p "127.0.0.1:${PORT}:8000" \
  -v "$DATA:/data" \
  -m 6G -c 6 \
  "$IMAGE"

echo "锐消 RedactX 已启动：http://127.0.0.1:${PORT}"
