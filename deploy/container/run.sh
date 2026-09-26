#!/usr/bin/env bash
# 用 Apple container 在本机运行锐消 RedactX。
# 用法：deploy/container/run.sh [端口，默认 8080]
set -euo pipefail

PORT="${1:-8080}"
NAME="redactx"
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
