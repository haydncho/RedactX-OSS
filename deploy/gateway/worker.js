// redactx.toras.dev 上的控制台与接口：介绍站由 Pages 直接提供（自定义域名），本 Worker 只挂在控制台与接口的路径上
// （见 wrangler.toml 的 routes），转到本机脱敏服务（经 Cloudflare 隧道）。本机关机时介绍站照常可用，控制台才不可用。
// 路由写得宽一些（/app*、/docs*）时，不属于控制台的路径仍交回介绍站。
// 转发时路径原样保留（服务端 /app 与 / 都是控制台），请求体按流转发，不在这里缓存或改写任何内容。
const APP_PATHS = /^\/(?:app\/?$|v1\/|static\/|docs\/?$|openapi\.json$)/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const toApp = APP_PATHS.test(url.pathname);
    const target = new URL(url.pathname + url.search, toApp ? env.APP_ORIGIN : env.SITE_ORIGIN);
    return fetch(new Request(target, request), { redirect: "manual" });
  },
};
