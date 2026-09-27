#!/usr/bin/env bash
# 发布介绍站 site/ 到 Cloudflare Pages（项目 redactx-oss）。
# 用法：deploy/site.sh [在线演示地址]
#   给了地址时，/app 跳转到该地址（本机服务的临时隧道，地址每次重启都会变）；不给时 /app 跳到 GitHub。
# 需要先登录：npx wrangler@4 login。本机 ~/.npm 有权限问题时，先设置 npm_config_cache 指向一个可写的目录。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-https://github.com/haydncho/RedactX-OSS}"
printf '# 在线演示入口（由 deploy/site.sh 生成，不入库）\n/app  %s  302\n' "$APP" > "$ROOT/site/_redirects"
npx --yes wrangler@4 pages deploy "$ROOT/site" --project-name redactx-oss --branch main --commit-dirty=true
