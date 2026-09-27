/* 锐消 RedactX 接口文档页：读取 /openapi.json 生成接口列表。不加载任何外部资源。 */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids) if (c != null) n.append(c);
    return n;
  };
  const icon = (name) => {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "ic");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(NS, "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  };
  const toast = (msg) => {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => t.classList.remove("show"), 1800);
  };
  const apiKey = () => {
    try { return JSON.parse(localStorage.getItem("redactx.apikey") || '""'); } catch { return ""; }
  };

  let spec = null;
  const resolve = (s) => {
    if (s && s.$ref) return resolve(s.$ref.replace("#/", "").split("/").reduce((o, k) => o[k], spec));
    return s || {};
  };
  function typeOf(s) {
    s = resolve(s);
    if (s.anyOf) return s.anyOf.map(typeOf).filter((t) => t !== "null").join(" | ");
    if (s.format === "binary" || s.contentMediaType === "application/octet-stream") return "文件";
    if (s.type === "array") return `${typeOf(s.items)}[]`;
    return s.type || "object";
  }

  const slug = (m, p) => `${m}-${p}`.replace(/[^a-zA-Z0-9]+/g, "-").replace(/-+$/, "");
  const pathNode = (p) => {
    const span = el("span", { class: "path" });
    for (const part of p.split(/(\{[^}]+\})/)) span.append(part.startsWith("{") ? el("em", { text: part }) : part);
    return span;
  };

  function curl(method, path, op) {
    const base = location.origin;
    const lines = [`curl -X ${method.toUpperCase()} "${base}${path.replace(/\{(\w+)\}/g, (_, n) => (n === "page" ? "1" : "<job_id>"))}"`];
    if ((op.parameters || []).some((p) => p.name === "x-api-key")) lines.push(`  -H "X-API-Key: <你的 API Key>"`);
    const body = op.requestBody && resolve(Object.values(op.requestBody.content)[0].schema);
    for (const [name, s] of Object.entries(body?.properties || {})) {
      if (typeOf(s) === "文件") lines.push(`  -F "${name}=@病案.pdf"`);
      else if (name === "options") lines.push(`  -F 'options={"default_style":"label","mode":"strict"}'`);
    }
    if (path.endsWith("/result") || path === "/v1/redact") lines.push(`  -o redacted.pdf`);
    return lines.join(" \\\n");
  }

  function paramTable(rows) {
    return el("table", { class: "api-table" },
      el("thead", {}, el("tr", {}, ...["名称", "位置", "类型", "必填", "说明"].map((h) => el("th", { text: h })))),
      el("tbody", {}, ...rows.map((r) => el("tr", {},
        el("td", {}, el("code", { text: r.name })), el("td", { text: r.in }), el("td", { class: "mono", text: r.type }),
        el("td", { text: r.required ? "是" : "否" }), el("td", { text: r.desc || "" })))));
  }

  const IN = { path: "路径", query: "查询", header: "请求头", form: "表单" };

  function endpoint(method, path, op) {
    const id = slug(method, path);
    const card = el("section", { class: "api-card api-op", id });
    card.append(el("div", { class: "op-head" }, el("span", { class: `method m-${method}`, text: method.toUpperCase() }), pathNode(path)));
    card.append(el("h3", { text: op.summary || "" }));
    if (op.description) card.append(el("p", { text: op.description }));

    const rows = (op.parameters || []).map((p) => ({
      name: p.name === "x-api-key" ? "X-API-Key" : p.name, in: IN[p.in] || p.in, type: typeOf(p.schema),
      required: p.required, desc: p.description,
    }));
    const body = op.requestBody && resolve(Object.values(op.requestBody.content)[0].schema);
    const req = new Set(body?.required || []);
    for (const [name, s] of Object.entries(body?.properties || {})) {
      rows.push({ name, in: IN.form, type: typeOf(s), required: req.has(name), desc: resolve(s).description });
    }
    if (rows.length) card.append(el("h4", { text: "参数" }), paramTable(rows));

    const resp = Object.entries(op.responses || {}).filter(([code]) => code !== "422");
    if (resp.length) {
      card.append(el("h4", { text: "响应" }), el("ul", { class: "resp" }, ...resp.map(([code, r]) =>
        el("li", {}, el("span", { class: `code c${code[0]}`, text: code }), r.description === "Successful Response" ? "成功" : r.description || ""))));
    }

    const text = curl(method, path, op);
    const copy = el("button", { class: "copy", type: "button", "aria-label": "复制示例", onclick: async () => {
      try { await navigator.clipboard.writeText(text); toast("已复制"); } catch { toast("复制失败，请手动选择"); }
    } }, icon("copy"), "复制");
    card.append(el("h4", { text: "示例" }), el("div", { class: "code-wrap" }, el("pre", { class: "code" }, el("code", { text })), copy));
    return { card, id };
  }

  async function catalogTables() {
    try {
      const key = apiKey();
      const r = await fetch("/v1/catalog", { headers: key ? { "X-API-Key": key } : {} });
      if (!r.ok) return;
      const c = await r.json();
      const box = $("#catalog-tables");
      box.append(el("h4", { text: "实体类型" }), el("table", { class: "api-table" },
        el("thead", {}, el("tr", {}, ...["code", "名称", "默认勾选", "默认样式"].map((h) => el("th", { text: h })))),
        el("tbody", {}, ...c.entities.map((e) => el("tr", {}, el("td", {}, el("code", { text: e.code })), el("td", { text: e.name }),
          el("td", { text: e.default ? "是" : "否" }), el("td", {}, el("code", { text: e.default_style })))))));
      box.append(el("h4", { text: "打码样式" }), el("table", { class: "api-table" },
        el("thead", {}, el("tr", {}, ...["code", "名称", "说明"].map((h) => el("th", { text: h })))),
        el("tbody", {}, ...c.styles.map((s) => el("tr", {}, el("td", {}, el("code", { text: s.code })), el("td", { text: s.name }), el("td", { text: s.desc }))))));
    } catch { /* 需要 API Key 时不显示这两张表 */ }
  }

  async function init() {
    spec = await (await fetch("/openapi.json")).json();
    $("#api-title").textContent = `${spec.info.title} 接口`;
    $("#api-desc").textContent = spec.info.description || "";
    $("#api-base").textContent = location.origin;
    $("#api-version").textContent = spec.info.version;
    const bv = $("#brand-ver"); if (bv) { bv.textContent = `v${spec.info.version}`; bv.hidden = false; }

    const nav = $("#api-nav");
    nav.append(el("div", { class: "nav-group" }, el("div", { class: "nav-title", text: "说明" }),
      el("a", { href: "#sec-auth", text: "认证" }), el("a", { href: "#sec-errors", text: "错误格式" }), el("a", { href: "#sec-options", text: "options 字段" })));

    const tags = (spec.tags || []).map((t) => t.name);
    const groups = new Map(tags.map((t) => [t, []]));
    for (const [path, ops] of Object.entries(spec.paths)) {
      for (const [method, op] of Object.entries(ops)) {
        const t = (op.tags || ["其他"])[0];
        if (!groups.has(t)) groups.set(t, []);
        groups.get(t).push([method, path, op]);
      }
    }
    const main = $("#api-endpoints");
    for (const [tag, ops] of groups) {
      if (!ops.length) continue;
      const info = (spec.tags || []).find((t) => t.name === tag);
      main.append(el("h2", { class: "tag-title", id: `tag-${tag}` }, tag, info?.description ? el("span", { text: info.description }) : null));
      const g = el("div", { class: "nav-group" }, el("div", { class: "nav-title", text: tag }));
      for (const [method, path, op] of ops) {
        const { card, id } = endpoint(method, path, op);
        main.append(card);
        g.append(el("a", { href: `#${id}` }, el("span", { class: `method m-${method}`, text: method.toUpperCase() }), el("span", { class: "nav-label", text: op.summary || path })));
      }
      nav.append(g);
    }
    catalogTables();
  }

  // 窄屏的接口目录隐藏层
  function initNavDrawer() {
    const nav = $("#api-nav"), btn = $("#nav-open"), backdrop = $("#nav-backdrop");
    const narrow = matchMedia("(max-width: 900px)");
    const isOpen = () => nav.classList.contains("open");
    function open() {
      nav.classList.add("open"); backdrop.hidden = false; document.body.classList.add("nav-open");
      btn.setAttribute("aria-expanded", "true"); nav.setAttribute("role", "dialog"); nav.setAttribute("aria-modal", "true");
      $("#nav-close").focus();
    }
    function close(returnFocus = true) {
      if (!isOpen()) return;
      nav.classList.remove("open"); backdrop.hidden = true; document.body.classList.remove("nav-open");
      btn.setAttribute("aria-expanded", "false"); nav.removeAttribute("role"); nav.removeAttribute("aria-modal");
      if (returnFocus) btn.focus();
    }
    btn.addEventListener("click", () => (isOpen() ? close() : open()));
    $("#nav-close").addEventListener("click", () => close());
    backdrop.addEventListener("click", () => close());
    // 点目录项：跳转后收起
    nav.addEventListener("click", (e) => { if (e.target.closest("a") && narrow.matches) close(false); });
    document.addEventListener("keydown", (e) => {
      if (!isOpen()) return;
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key === "Tab") { // 焦点留在目录里
        const f = [...nav.querySelectorAll("a, button")].filter((x) => x.offsetParent);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    });
    narrow.addEventListener("change", () => { if (!narrow.matches) close(false); });
  }

  initNavDrawer();
  init().catch(() => { $("#api-endpoints").append(el("p", { class: "api-foot", text: "读取 /openapi.json 失败" })); });
})();
