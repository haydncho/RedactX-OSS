#!/usr/bin/env bash
# 发布介绍站 site/ 到 Cloudflare Pages（项目 redactx-oss）。
# 用法：deploy/site.sh [在线演示地址]
#   正式地址是 https://redactx.toras.dev：介绍站是本 Pages 项目的自定义域名，/app（控制台）与接口由
#   deploy/gateway 的 Worker 转到本机服务的正式隧道。这里的 /app 跳转只对直接访问 pages.dev 的人生效，默认跳到正式地址。
# 需要先登录：npx wrangler@4 login。本机 ~/.npm 有权限问题时，先设置 npm_config_cache 指向一个可写的目录。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-https://redactx.toras.dev/app}"
printf '# 在线演示入口（由 deploy/site.sh 生成，不入库）\n/app  %s  302\n' "$APP" > "$ROOT/site/_redirects"
npx --yes wrangler@4 pages deploy "$ROOT/site" --project-name redactx-oss --branch main --commit-dirty=true
