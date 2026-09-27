#!/usr/bin/env bash
# 后台管理：在运行中的容器里生成、列出、吊销 API Key，清空任务。参数同 python -m service.admin。
#   deploy/keys.sh keys create "演示-张医生"
#   deploy/keys.sh keys list
#   deploy/keys.sh keys revoke key_xxxxxxxxxxxx [--purge]
#   deploy/keys.sh jobs clear --yes
# 容器名默认 redactx-oss，可用环境变量 REDACTX_CONTAINER 改。
set -euo pipefail
NAME="${REDACTX_CONTAINER:-redactx-oss}"
if ! container inspect "$NAME" >/dev/null 2>&1; then
  echo "容器 $NAME 没有运行：先用 deploy/container/run.sh 或 dev.sh 启动" >&2
  exit 1
fi
exec container exec "$NAME" python -m service.admin "$@"
