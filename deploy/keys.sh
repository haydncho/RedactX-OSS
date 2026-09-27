#!/usr/bin/env bash
# 后台管理：在运行中的容器里生成、列出、吊销 API Key，清空任务。参数同 python -m service.admin。
#   deploy/keys.sh keys create "演示-张医生"
#   deploy/keys.sh keys list
#   deploy/keys.sh keys revoke key_xxxxxxxxxxxx [--purge]
#   deploy/keys.sh jobs clear --yes
# 容器名默认 redactx-oss（REDACTX_CONTAINER 可改）。自动识别 Apple container 与 Docker；
# 两者都有时可用 REDACTX_RUNTIME=container 或 docker 指定。
set -euo pipefail
NAME="${REDACTX_CONTAINER:-redactx-oss}"
RUNTIME="${REDACTX_RUNTIME:-}"
if [ -z "$RUNTIME" ]; then
  if command -v container >/dev/null 2>&1 && container inspect "$NAME" >/dev/null 2>&1; then
    RUNTIME=container
  elif command -v docker >/dev/null 2>&1 && docker inspect "$NAME" >/dev/null 2>&1; then
    RUNTIME=docker
  else
    echo "没有找到运行中的容器 $NAME：先用 deploy/container/run.sh、dev.sh 或 docker compose 启动" >&2
    exit 1
  fi
fi
if [ "$RUNTIME" = docker ]; then
  exec docker exec "$NAME" python -m service.admin "$@"
fi
exec container exec "$NAME" python -m service.admin "$@"
