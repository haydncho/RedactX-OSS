/* 锐消 RedactX Web 页：选择脱敏字段与样式、上传、查看进度、对比预览、下载。 */
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

  // 图标：引用 index.html 里定义的 <symbol>
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

  const STORE_KEY = "redactx.settings.v1";
  const KEY_KEY = "redactx.apikey";
  // 原文件名常含患者姓名，服务端不保存；只记在本浏览器里（按任务 ID），任务删除或过期后一并清掉
  const NAMES_KEY = "redactx.jobnames.v1";
  const jobNames = {
    all() { return store.get(NAMES_KEY, {}) || {}; },
    get(id) { return this.all()[id] || ""; },
    set(id, name) { const m = this.all(); m[id] = name; store.set(NAMES_KEY, m); },
    drop(id) { const m = this.all(); if (id in m) { delete m[id]; store.set(NAMES_KEY, m); } },
    keepOnly(ids) { const m = this.all(), keep = new Set(ids); let changed = false; for (const k of Object.keys(m)) if (!keep.has(k)) { delete m[k]; changed = true; } if (changed) store.set(NAMES_KEY, m); },
  };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 隐私模式下忽略 */ } },
  };

  let catalog = null;
  let state = {
    preset: "audit",
    entities: null,        // Set of codes
    styles: {},            // code -> style
    mode: "strict",
    label_text: "type",
    dpi: 200,
    verify: "auto",        // auto 只自检扫描页 | on | off
    keep_source: false,    // 保留打码前的页面，复核时可删框、改框
    rv_type: "PERSON",     // 复核时新框的类型
    custom: "",
    retention: "24",
  };
  let job = null;          // { id, pages, name, kind }
  let report = null;
  let page = 1;
  let view = "compare";
  let pollTimer = null;
  let typeFilter = null;
  const blobCache = new Map();
  let rev = 0;             // 复核保存后递增，让预览图重新加载
  let maxUploadMB = 30;    // 单个文件上传上限，启动时以 /v1/health 返回的为准

  // ---------- 接口 ----------
  function headers() {
    const k = store.get(KEY_KEY, "");
    return k ? { "X-API-Key": k } : {};
  }
  async function api(path, opts = {}) {
    const r = await fetch(path, { ...opts, headers: { ...headers(), ...(opts.headers || {}) } });
    if (!r.ok) {
      let msg = `请求失败（${r.status}）`;
      try { const j = await r.json(); if (j.error) msg = j.error.message || msg; } catch { /* 非 JSON */ }
      const e = new Error(msg); e.status = r.status; throw e;
    }
    return r;
  }
  // 设了 API Key 时图片要带请求头取回，转成 blob URL 显示。最多留 80 张，超出时释放最早的
  const BLOB_MAX = 80;
  async function imgSrc(path) {
    if (!store.get(KEY_KEY, "")) return path;
    if (blobCache.has(path)) {
      const u = blobCache.get(path); blobCache.delete(path); blobCache.set(path, u);  // 最近用过的排到最后
      return u;
    }
    const r = await api(path);
    const url = URL.createObjectURL(await r.blob());
    blobCache.set(path, url);
    while (blobCache.size > BLOB_MAX) {
      const [k, u] = blobCache.entries().next().value;
      URL.revokeObjectURL(u); blobCache.delete(k);
    }
    return url;
  }
  function clearBlobs() {
    for (const u of blobCache.values()) URL.revokeObjectURL(u);
    blobCache.clear();
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 2600);
  }

  // ---------- 设置面板 ----------
  const groupColor = (g) => `var(--c-${g})`;
  const entityInfo = (code) => catalog.entities.find((e) => e.code === code);

  function applyPreset(code) {
    const p = catalog.presets.find((x) => x.code === code);
    if (!p) return;
    state.preset = code;
    state.styles = {};
    for (const e of catalog.entities) {
      state.styles[e.code] = p.overrides[e.code] || (code === "audit" ? e.default_style : p.default_style);
    }
    renderEntities();
    renderPresets();
    save();
  }

  function renderPresets() {
    const box = $("#presets");
    box.replaceChildren();
    box.setAttribute("aria-busy", "false");
    for (const p of catalog.presets) {
      const sty = p.code === "audit" ? "label" : p.default_style;
      box.append(el("button", {
        type: "button", class: "preset", role: "radio", "aria-checked": String(state.preset === p.code),
        onclick: () => applyPreset(p.code),
      },
      el("div", { class: "swatch" }, el("span", { style: "width:34%" }), el("i", { class: `sty sty-${sty}` }), el("span", { style: "width:22%" })),
      el("b", { text: p.name }), el("small", { text: p.desc, title: p.desc })));
    }
  }

  function renderEntities() {
    const root = $("#entity-groups");
    root.replaceChildren();
    root.setAttribute("aria-busy", "false");
    for (const g of catalog.groups) {
      const ents = catalog.entities.filter((e) => e.group === g.key && e.code !== "CUSTOM");
      if (!ents.length) continue;
      const wrap = el("div", { class: "group" }, el("div", { class: "group-title" }, el("i", { style: `background:${groupColor(g.key)}` }), g.name));
      for (const e of ents) {
        const on = state.entities.has(e.code);
        const id = `ent-${e.code}`;
        const sel = el("select", { "aria-label": `${e.name}的打码样式`, onchange: (ev) => {
          state.styles[e.code] = ev.target.value; state.preset = "custom"; renderPresets(); sw.className = `sty sty-${ev.target.value}`; save();
        } });
        for (const s of catalog.styles) sel.append(el("option", { value: s.code, text: s.name, title: s.desc }));
        sel.value = state.styles[e.code] || e.default_style;
        const sw = el("i", { class: `sty sty-${sel.value}` });
        const cb = el("input", { type: "checkbox", id, onchange: (ev) => {
          ev.target.checked ? state.entities.add(e.code) : state.entities.delete(e.code);
          row.classList.toggle("off", !ev.target.checked); updateSelToggle(); save();
        } });
        cb.checked = on;
        // 水印在识别前整页擦除，打码样式不适用
        const pick = e.code === "WATERMARK" ? el("div", { class: "style-na", title: "只擦除水印像素，下面的正文保留" }, "直接擦除")
          : el("div", { class: "style-pick" }, sw, sel);
        const row = el("div", { class: `ent${on ? "" : " off"}` }, cb, el("label", { for: id, text: e.name, title: e.name }), pick);
        wrap.append(row);
      }
      root.append(wrap);
    }
    root.querySelectorAll("select").forEach((s) => UI.makeSelect(s));
    updateSelToggle();
  }

  // 全选按钮随勾选状态变化：一个没选显示“全选”，全部选中显示“全不选”，部分选中显示“已选 N 项”（点击全选）
  const selectable = () => catalog.entities.filter((e) => e.code !== "CUSTOM").map((e) => e.code);
  function updateSelToggle() {
    const btn = $("#sel-toggle");
    const codes = selectable();
    const n = codes.filter((c) => state.entities.has(c)).length;
    const [label, ic, title] = n === 0 ? ["全选", "check-all", "全部选中"]
      : n === codes.length ? ["全不选", "square", "全部取消"]
      : [`已选 ${n} 项`, "minus-square", `共 ${codes.length} 项，点击全选`];
    btn.querySelector(".lb").textContent = label;
    btn.querySelector("use").setAttribute("href", `#i-${ic}`);
    btn.title = title;
    btn.dataset.all = String(n === codes.length);
  }

  function bindSeg(id, key, cast = (v) => v) {
    const seg = $(id);
    const sync = () => seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(String(state[key]) === b.dataset.v)));
    seg.addEventListener("click", (ev) => {
      const b = ev.target.closest("button"); if (!b) return;
      state[key] = cast(b.dataset.v); sync(); save();
      if (key === "view") renderView();
    });
    sync();
    return sync;
  }

  function save() {
    store.set(STORE_KEY, { ...state, entities: [...state.entities] });
  }

  function load() {
    const s = store.get(STORE_KEY, null);
    if (s) {
      state = { ...state, ...s, entities: new Set(s.entities || []) };
    }
    if (!state.entities || !state.entities.size && !s) {
      state.entities = new Set(catalog.entities.filter((e) => e.default).map((e) => e.code));
    }
    if (!Object.keys(state.styles).length) applyPreset("audit");
  }

  function buildOptions() {
    const custom = $("#custom-words").value.split(/\n+/).map((w) => w.trim()).filter((w) => w.length >= 2);
    const entities = [...state.entities];
    if (custom.length) entities.push("CUSTOM");
    return {
      entities,
      default_style: "label",
      styles: Object.fromEntries(entities.map((c) => [c, state.styles[c] || entityInfo(c)?.default_style || "label"])),
      custom_words: custom,
      mode: state.mode,
      label_text: state.label_text,
      dpi: Number(state.dpi),
      verify: state.verify === "auto" ? "auto" : state.verify === "on",
      keep_source: !!state.keep_source,
    };
  }

  // ---------- 上传与任务 ----------
  function pickFile() { $("#file").click(); }

  function upload(file) {
    if (!state.entities.size && !$("#custom-words").value.trim()) { toast("请至少选择一类脱敏字段"); return; }
    // 先在本地检查大小，超限不上传（服务端同样会拒绝）
    if (file.size > maxUploadMB * 1024 * 1024) { toast(`文件 ${(file.size / 1048576).toFixed(1)} MB，超过单个文件 ${maxUploadMB} MB 的上限`); return; }
    const kind = (file.name.split(".").pop() || "").toUpperCase().slice(0, 4);
    job = { id: null, name: file.name, kind, pages: 0 };
    report = null; page = 1; clearBlobs(); beforePage = 0;
    stopReview();
    showJob();
    setProgress(0, "上传中");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("options", JSON.stringify(buildOptions()));
    fd.append("retention_hours", $("#opt-retention").value);
    const pw = $("#opt-password").value; if (pw) fd.append("password", pw);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/v1/jobs");
    for (const [k, v] of Object.entries(headers())) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProgress(0.08 * e.loaded / e.total, `上传中 ${Math.round(100 * e.loaded / e.total)}%`); };
    xhr.onload = () => {
      let body = {}; try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 300) { showError(body.error?.message || `上传失败（${xhr.status}）`); return; }
      job.id = body.job_id; job.pages = body.pages;
      jobNames.set(job.id, file.name);
      $("#job-meta").textContent = body.pages ? `${body.pages} 页 · 已提交` : "已提交 · 先转换为 PDF";
      poll();
    };
    xhr.onerror = () => showError("上传失败，请确认服务正在运行");
    xhr.send(fd);
  }

  function showJob() {
    stopReview();
    $("#drop").hidden = true;
    $("#job").hidden = false;
    $("#job-name").textContent = job.name || job.id;
    $("#job-name").title = job.name || job.id;  // 省略时悬停看全文
    $("#job-kind").textContent = job.kind || "PDF";
    $("#job-meta").textContent = job.pages ? `${job.pages} 页` : "—";
    $("#job-error").hidden = true;
    $("#result").hidden = true;
    $("#progress").hidden = false;
    $("#btn-download").hidden = true;
    $("#btn-delete").hidden = true;
    $("#btn-review").hidden = true;
  }

  function setProgress(p, msg) {
    $("#progress-fill").style.width = `${Math.max(2, Math.round(p * 100))}%`;
    $("#progress-pct").textContent = `${Math.round(p * 100)}%`;
    $("#progress-msg").textContent = msg;
  }

  function showError(msg) {
    clearTimeout(pollTimer);
    $("#progress").hidden = true;
    const a = $("#job-error"); a.textContent = msg; a.hidden = false;
    $("#btn-delete").hidden = !job?.id;
    refreshHistory();
  }

  async function poll() {
    clearTimeout(pollTimer);
    try {
      const j = await (await api(`/v1/jobs/${job.id}`)).json();
      if (j.status === "failed") { showError(j.error || "处理失败"); return; }
      if (j.status === "succeeded") { await loadResult(j); return; }
      setProgress(0.08 + 0.92 * (j.progress || 0), j.message || "处理中");
      pollTimer = setTimeout(poll, 700);
    } catch (e) {
      showError(e.message);
    }
  }

  async function loadResult(j) {
    setProgress(1, "完成");
    report = await (await api(`/v1/jobs/${job.id}/report`)).json();
    job.pages = report.pages;
    const secs = report.elapsed_sec;
    $("#job-meta").textContent = `${report.pages} 页 · 用时 ${secs < 60 ? secs + " 秒" : (secs / 60).toFixed(1) + " 分钟"} · 保留至 ${new Date(j.expires * 1000).toLocaleString("zh-CN", { hour12: false })}`;
    $("#progress").hidden = true;
    $("#result").hidden = false;
    $("#result").classList.remove("loading");
    const dl = $("#btn-download");
    dl.hidden = false;
    dl.href = `/v1/jobs/${job.id}/result`;
    dl.onclick = async (ev) => {
      if (!store.get(KEY_KEY, "")) return; // 无 Key 时直接走链接下载
      ev.preventDefault();
      const r = await api(`/v1/jobs/${job.id}/result`);
      const url = URL.createObjectURL(await r.blob());
      const a = el("a", { href: url, download: `redacted-${job.id}.${report.output.split(".").pop()}` });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    };
    $("#btn-delete").hidden = false;
    $("#btn-review").hidden = false;
    renderSummary();
    renderThumbs();
    gotoPage(1);
    refreshHistory();
  }

  // ---------- 预览 ----------
  const itemsOf = (p) => ((reviewing ? draft : report?.items) || []).filter((it) => it.page === p);
  const preview = (p, v) => `/v1/jobs/${job.id}/preview/${p}?v=${v}${rev ? `&r=${rev}` : ""}`;

  function renderSummary() {
    const counts = report.counts || {};
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    $("#sum-total").textContent = total;
    $("#sum-pages").textContent = report.pages;
    $("#sum-mini").textContent = `${total} 处`;
    const ul = $("#counts"); ul.replaceChildren();
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    for (const [code, n] of entries) {
      const info = entityInfo(code) || { name: code, group: "people" };
      const li = el("li", { class: typeFilter === code ? "active" : "", title: "点击只看这一类", onclick: () => { typeFilter = typeFilter === code ? null : code; renderSummary(); renderBoxes(); } },
        el("i", { style: `background:${groupColor(info.group)}` }), el("span", { class: "lb", title: info.name, text: info.name }), el("span", { class: "n num", text: n }));
      ul.append(li);
    }
    if (!entries.length) ul.append(el("li", { text: "未发现需要遮盖的内容" }));
    const v = report.verification;
    const wmObj = report.watermark_objects ? `；已从 PDF 结构中删除水印对象 ${report.watermark_objects} 个` : "";
    const vPages = v?.pages != null ? `（${v.pages} 页）` : "";
    $("#sum-foot").textContent = (v?.enabled ? `出厂自检${vPages}：补打 ${v.residual_hits} 处` : v?.mode === "auto" ? "无扫描页，未自检" : "未开启出厂自检") + wmObj;
  }

  function renderThumbs() {
    const box = $("#thumbs"); box.replaceChildren();
    const io = new IntersectionObserver(async (entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        const img = en.target.querySelector("img");
        if (!img.src) {
          const done = () => img.closest(".thumb")?.classList.remove("img-loading");
          img.addEventListener("load", done, { once: true });
          img.addEventListener("error", done, { once: true });
          img.src = await imgSrc(preview(img.dataset.p, "after"));
        }
        io.unobserve(en.target);
      }
    }, { root: box, rootMargin: "200px" });
    const per = countsByPage();
    for (let p = 1; p <= report.pages; p++) {
      const n = per[p] || 0;
      const b = el("button", { class: "thumb img-loading", type: "button", "aria-label": `第 ${p} 页`, onclick: () => gotoPage(p) },
        el("img", { alt: "", "data-p": p }), el("span", { class: "tn", text: p }), n ? el("span", { class: "tc", text: n }) : null);
      box.append(b); io.observe(b);
    }
  }

  async function gotoPage(p) {
    page = Math.min(Math.max(1, p), report.pages);
    $("#pg-label").textContent = `${page} / ${report.pages}`;
    $("#pg-prev").disabled = page <= 1;
    $("#pg-next").disabled = page >= report.pages;
    document.querySelectorAll(".thumb").forEach((t, i) => t.setAttribute("aria-current", String(i + 1 === page)));
    // 只在缩略图列（手机上是横排）里滚动到当前页，不带动整个页面
    const cur = document.querySelectorAll(".thumb")[page - 1];
    if (cur) {
      const box = $("#thumbs"), b = box.getBoundingClientRect(), c = cur.getBoundingClientRect();
      if (c.left < b.left) box.scrollLeft -= b.left - c.left + 4; else if (c.right > b.right) box.scrollLeft += c.right - b.right + 4;
      if (c.top < b.top) box.scrollTop -= b.top - c.top + 4; else if (c.bottom > b.bottom) box.scrollTop += c.bottom - b.bottom + 4;
    }
    // 原件图只在对比、原件视图里用到：只看脱敏后时不下载，切换视图时再补
    const pg = page, needBefore = state.view !== "after";
    const [a, b] = await Promise.all([imgSrc(preview(pg, "after")), needBefore ? imgSrc(preview(pg, "before")) : null]);
    if (pg !== page) return;  // 等待期间又翻了页
    const img = $("#img-after");
    if (img.getAttribute("src") !== a) $("#stage").classList.add("img-loading");
    img.src = a;
    if (b) { $("#img-before").src = b; beforePage = pg; }
    if (img.complete && img.naturalWidth) $("#stage").classList.remove("img-loading");
    renderBoxes();
    renderPageItems();
    renderView();
  }

  let dragRaf = 0;
  function paintBox(it) {
    const d = [...$("#boxes").children].find((n) => n._item === it);
    if (!d) { renderBoxes(); return; }
    const [x0, y0, x1, y1] = it.box;
    Object.assign(d.style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` });
  }

  function renderBoxes() {
    const box = $("#boxes"); box.replaceChildren();
    if (!$("#show-boxes").checked && !reviewing) return;
    for (const it of itemsOf(page)) {
      const info = entityInfo(it.type) || { group: "people", name: it.type };
      const [x0, y0, x1, y1] = it.box;
      let cls = "box";
      if (typeFilter && typeFilter !== it.type && !reviewing) cls += " dim";
      if (reviewing) {
        if (canEdit(it)) cls += " edit";
        if (it._new) cls += " new";
        if (it === sel) cls += " sel";
      }
      const d = el("div", { class: cls, title: reviewing && !canEdit(it) ? `${info.name}（未保留原件，不能删改）` : info.name,
        style: `left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%;--bc:${groupColor(info.group)}` });
      d._item = it;
      if (reviewing && it === sel && canEdit(it)) d.append(el("span", { class: "rs", title: "拖动调整大小" }));
      box.append(d);
    }
  }

  // ---------- 复核：拖出新框；保留原件的任务还可点选后拖动、拉伸、删除 ----------
  let reviewing = false, draft = null, sel = null, dirty = 0, viewBefore = null, exportEnabled = false;
  const editable = () => !!report?.review?.editable;
  const canEdit = (it) => editable() || !!it._new;

  function startReview() {
    if (!report) return;
    reviewing = true; sel = null; dirty = 0;
    draft = report.items.map((it) => ({ ...it }));
    viewBefore = state.view; state.view = "after";
    document.querySelectorAll("#seg-view button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === "after")));
    renderView(); syncReview(); renderBoxes(); renderPageItems();
  }

  function stopReview() {
    if (!reviewing) return;
    reviewing = false; draft = null; sel = null; dirty = 0;
    if (viewBefore) { state.view = viewBefore; viewBefore = null; document.querySelectorAll("#seg-view button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === state.view))); renderView(); }
    syncReview();
    if (report) { renderBoxes(); renderPageItems(); }
  }

  function syncReview() {
    $("#btn-review").setAttribute("aria-pressed", String(reviewing));
    $("#review-bar").hidden = !reviewing;
    $("#stage").classList.toggle("reviewing", reviewing);
    if (!reviewing) return;
    $("#rv-hint").textContent = editable() ? "拖出新框，点选后可调整"
      : report.review?.finished ? "复核已完成，只能再加框：拖出新框" : "未保留原件，只能加框：拖出新框";
    $("#rv-hint").title = $("#rv-hint").textContent + (editable() ? "" : "（打码前的页面已删除，已有的框不能删除或修改）");
    $("#rv-del").disabled = !(sel && canEdit(sel));
    $("#rv-save").disabled = !dirty;
    const pages = changedPages();
    const d = $("#rv-dirty"); d.textContent = dirty ? `未保存 ${dirty} 处修改${pages.size ? `，涉及 ${pages.size} 页` : ""}` : "没有修改"; d.classList.toggle("on", !!dirty);
    document.querySelectorAll(".thumb").forEach((t, i) => t.classList.toggle("edited", pages.has(i + 1)));
    // 选中框时，类型下拉框显示并可修改它的类型
    const ty = $("#rv-type");
    if (sel && [...ty.options].some((o) => o.value === sel.type)) ty.value = sel.type;
    else if (!sel && [...ty.options].some((o) => o.value === state.rv_type)) ty.value = state.rv_type;
    $("#rv-type-label").textContent = sel && canEdit(sel) ? "所选框类型" : "新框类型";
    $("#rv-finish").hidden = !editable();
    $("#rv-export").hidden = !(exportEnabled && editable());
    $("#rv-export").disabled = !!dirty;
    $("#rv-export").title = dirty ? "先保存修改再导出" : "打码前的原始页面与复核后的框（COCO 格式），供训练检测模型；含真实内容";
  }

  const touched = () => { dirty++; syncReview(); renderBoxes(); };
  const itemKey = (it) => `${it.page}|${it.type}|${it.box.map((v) => v.toFixed(4)).join(",")}|${it.style || ""}`;
  // 草稿与已保存的报告不同的页
  function changedPages() {
    const out = new Set();
    if (!reviewing || !report) return out;
    const count = (items) => { const m = new Map(); for (const it of items) { const k = itemKey(it); m.set(k, (m.get(k) || 0) + 1); } return m; };
    const a = count(report.items), b = count(draft);
    for (const [k, n] of a) if ((b.get(k) || 0) !== n) out.add(Number(k.split("|")[0]));
    for (const [k, n] of b) if ((a.get(k) || 0) !== n) out.add(Number(k.split("|")[0]));
    return out;
  }
  // 各页框数：一次遍历分组，不必每页筛一遍所有框
  function countsByPage() {
    const per = {};
    for (const it of (reviewing ? draft : report?.items) || []) per[it.page] = (per[it.page] || 0) + 1;
    return per;
  }
  function renderAllThumbCounts() {
    const per = countsByPage();
    document.querySelectorAll(".thumb").forEach((t, i) => {
      const n = per[i + 1] || 0; let c = t.querySelector(".tc");
      if (!c && n) { c = el("span", { class: "tc" }); t.append(c); }
      if (c) { c.textContent = n; c.hidden = !n; }
    });
  }

  function deleteSel() {
    if (!sel || !canEdit(sel)) return;
    draft.splice(draft.indexOf(sel), 1); sel = null; touched(); renderPageItems(); renderAllThumbCounts();
  }

  function selectNext(dir) {
    const items = itemsOf(page);
    if (!items.length) return;
    const k = sel ? items.indexOf(sel) : -1;
    sel = items[(k + dir + items.length) % items.length];
    syncReview(); renderBoxes();
  }

  function setType(code) {
    if (sel && canEdit(sel)) {
      if (sel.type !== code) { sel.type = code; delete sel.style; delete sel.alias; touched(); renderPageItems(); }
    } else {
      state.rv_type = code; save();
    }
    syncReview();
  }

  function nudge(dx, dy) {
    if (!sel || !canEdit(sel)) return;
    const r = $("#stage").getBoundingClientRect();
    const [x0, y0, x1, y1] = sel.box, w = x1 - x0, h = y1 - y0;
    const nx = Math.min(1 - w, Math.max(0, x0 + dx / r.width)), ny = Math.min(1 - h, Math.max(0, y0 + dy / r.height));
    sel.box = [nx, ny, nx + w, ny + h].map((v) => Math.round(v * 10000) / 10000);
    touched();
  }

  async function saveReview() {
    const btn = $("#rv-save"); btn.disabled = true;
    const items = draft.map(({ _new, ...it }) => (_new ? { page: it.page, type: it.type, box: it.box } : it));
    try {
      report = await (await api(`/v1/jobs/${job.id}/review`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) })).json();
    } catch (e) { toast(e.message); syncReview(); return; }
    rev++; clearBlobs(); beforePage = 0;
    toast("已保存并重新打码");
    stopReview(); startReview();
    renderSummary(); renderThumbs(); gotoPage(page); refreshHistory();
  }

  function initReview() {
    const sel_ = $("#rv-type");
    for (const e of catalog.entities.filter((e) => !["WATERMARK", "CUSTOM"].includes(e.code))) sel_.append(el("option", { value: e.code, text: e.name }));
    if ([...sel_.options].some((o) => o.value === state.rv_type)) sel_.value = state.rv_type;
    sel_.onchange = () => setType(sel_.value);
    UI.makeSelect(sel_);
    const discard = () => UI.confirmDialog("有未保存的修改，退出复核后这些修改会丢失。", { title: "放弃未保存的修改？", ok: "放弃修改", cancel: "继续复核", danger: true });
    $("#btn-review").onclick = async () => {
      if (!reviewing) return startReview();
      if (dirty && !(await discard())) return;
      stopReview();
    };
    $("#rv-cancel").onclick = async () => { if (!dirty || (await discard())) stopReview(); };
    $("#rv-del").onclick = deleteSel;
    $("#rv-save").onclick = saveReview;
    $("#rv-export").onclick = async () => {
      try {
        const r = await api(`/v1/jobs/${job.id}/export`);
        const url = URL.createObjectURL(await r.blob());
        const a = el("a", { href: url, download: `annotations-${job.id}.zip` });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
      } catch (e) { toast(e.message); }
    };
    const dlgFin = $("#dlg-finish");
    $("#rv-finish").onclick = () => { dlgFin.returnValue = ""; dlgFin.showModal(); };
    dlgFin.addEventListener("close", async () => {
      if (dlgFin.returnValue !== "ok" || !job?.id) return;
      if (dirty) await saveReview();
      try { report.review = await (await api(`/v1/jobs/${job.id}/review/finish`, { method: "POST" })).json(); } catch (e) { toast(e.message); return; }
      toast("复核完成，已删除保留的打码前页面");
      syncReview(); renderBoxes();
    });

    const layer = $("#boxes");
    let drag = null;
    const norm = (e) => { const r = $("#stage").getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]; };
    layer.addEventListener("pointerdown", (e) => {
      if (!reviewing || e.button !== 0 || panOn || spaceHeld) return;
      e.preventDefault(); e.stopPropagation();
      const [x, y] = norm(e);
      const hit = e.target.closest(".box")?._item;
      if (e.target.classList.contains("rs") && hit) {
        drag = { mode: "resize", it: hit, box0: [...hit.box] };
      } else if (hit) {
        sel = hit;
        drag = canEdit(hit) ? { mode: "move", it: hit, x, y, box0: [...hit.box] } : null;
        syncReview(); renderBoxes();
      } else {
        const it = { page, type: $("#rv-type").value, source: "manual", box: [x, y, x, y], _new: true };
        draft.push(it); sel = it;
        drag = { mode: "draw", it, x, y };
      }
      if (drag) { drag.moved = false; (() => { try { layer.setPointerCapture(e.pointerId); } catch { /* 指针已松开 */ } })(); }
    });
    layer.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const [x, y] = norm(e), b = drag.it.box;
      if (drag.mode === "draw") {
        drag.it.box = [Math.min(x, drag.x), Math.min(y, drag.y), Math.max(x, drag.x), Math.max(y, drag.y)];
      } else if (drag.mode === "move") {
        const [x0, y0, x1, y1] = drag.box0, w = x1 - x0, h = y1 - y0;
        const nx = Math.min(1 - w, Math.max(0, x0 + x - drag.x)), ny = Math.min(1 - h, Math.max(0, y0 + y - drag.y));
        drag.it.box = [nx, ny, nx + w, ny + h];
      } else {
        drag.it.box = [b[0], b[1], Math.max(b[0] + 0.004, x), Math.max(b[1] + 0.004, y)];
      }
      drag.moved = true;
      // 每帧最多重绘一次，而且只挪动被拖的这个框，不重建整页的框
      const it = drag.it;
      if (!dragRaf) dragRaf = requestAnimationFrame(() => { dragRaf = 0; paintBox(it); });
    });
    const end = () => {
      if (!drag) return;
      const { it, mode, moved } = drag; drag = null;
      if (dragRaf) { cancelAnimationFrame(dragRaf); dragRaf = 0; }
      it.box = it.box.map((v) => Math.round(v * 10000) / 10000);
      paintBox(it);
      if (mode === "draw" && (it.box[2] - it.box[0] < 0.005 || it.box[3] - it.box[1] < 0.004)) {
        draft.splice(draft.indexOf(it), 1); sel = null; syncReview(); renderBoxes(); return;
      }
      if (mode === "draw" || moved) { touched(); renderPageItems(); renderAllThumbCounts(); }
    };
    layer.addEventListener("pointerup", end);
    layer.addEventListener("pointercancel", end);
    // 复核快捷键；先于翻页、缩放等全局快捷键处理，处理过的不再往下传
    window.addEventListener("keydown", (e) => {
      if (!reviewing || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || document.querySelector("dialog[open]")) return;
      const done = () => { e.preventDefault(); e.stopImmediatePropagation(); };
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { done(); if (dirty) saveReview(); return; }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Delete" || e.key === "Backspace") { done(); deleteSel(); }
      else if (e.key === "Escape") { done(); sel = null; syncReview(); renderBoxes(); }
      else if (e.key === "Tab") { done(); selectNext(e.shiftKey ? -1 : 1); }
      else if (e.key === "[" || e.key === "]") {
        done();
        const opts = [...$("#rv-type").options].map((o) => o.value);
        const cur = opts.indexOf($("#rv-type").value);
        setType(opts[(cur + (e.key === "]" ? 1 : -1) + opts.length) % opts.length]);
      } else if (sel && canEdit(sel) && e.key.startsWith("Arrow")) {
        done();
        const s = e.shiftKey ? 10 : 1;
        nudge(e.key === "ArrowLeft" ? -s : e.key === "ArrowRight" ? s : 0, e.key === "ArrowUp" ? -s : e.key === "ArrowDown" ? s : 0);
      }
    });
    window.addEventListener("beforeunload", (e) => { if (reviewing && dirty) e.preventDefault(); });
  }


  const SRC_NAME = { rule: "规则", anchor: "字段锚定", "anchor-field": "填写区", propagate: "全文追踪", custom: "自定义词", color: "颜色", detector: "检测", "image-object": "图片对象", repeat: "跨页重复", watermark: "水印消除", verify: "自检补打", manual: "人工添加", ner: "正文识别", model: "检测模型", template: "表单模板", "beside-org": "院名旁图形" };
  function renderPageItems() {
    const ul = $("#page-items"); ul.replaceChildren();
    const items = itemsOf(page);
    if (!items.length) { ul.append(el("li", { text: "本页没有遮盖" })); return; }
    const agg = {};
    for (const it of items) { const k = `${it.type}|${it.source}`; agg[k] = (agg[k] || 0) + 1; }
    for (const [k, n] of Object.entries(agg)) {
      const [type, src] = k.split("|");
      const info = entityInfo(type) || { group: "people", name: type };
      const text = `${info.name} × ${n}`;
      ul.append(el("li", {}, el("i", { style: `background:${groupColor(info.group)}` }), el("span", { class: "lb", title: text, text }), el("span", { class: "src", text: SRC_NAME[src] || src })));
    }
  }

  let beforePage = 0;  // 原件图当前显示的是哪一页
  function renderView() {
    const stage = $("#stage");
    stage.classList.toggle("mode-after", state.view === "after");
    stage.classList.toggle("mode-before", state.view === "before");
    if (state.view !== "after" && job?.id && report && beforePage !== page) {
      const p = page;
      imgSrc(preview(p, "before")).then((b) => { if (p === page) { $("#img-before").src = b; beforePage = p; } }).catch(() => {});
    }
  }

  // ---------- 缩放：100% 为适应窗口；⌘/Ctrl + 滚轮、触控板捏合、工具条、键盘 + − 0 ----------
  const ZMIN = 0.25, ZMAX = 4, ZSTEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.25, 1.5, 2, 2.5, 3, 4];
  let zoom = 1;

  function fitWidth() {
    const v = $("#viewer"), img = $("#img-after");
    if (!img.naturalWidth) return 0;
    const cs = getComputedStyle(v);
    const aw = v.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const ah = v.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    return Math.max(40, Math.min(aw, ah * (img.naturalWidth / img.naturalHeight)));
  }

  // anchor：保持不动的点（相对视口的 clientX/Y），默认为画布中心
  function setZoom(z, anchor) {
    const v = $("#viewer"), stage = $("#stage");
    z = Math.min(ZMAX, Math.max(ZMIN, z));
    const w = fitWidth();
    if (!w) { zoom = z; return; }
    const vr = v.getBoundingClientRect();
    const ax = anchor ? anchor.x : vr.left + vr.width / 2, ay = anchor ? anchor.y : vr.top + vr.height / 2;
    const sr = stage.getBoundingClientRect();
    const fx = sr.width ? (ax - sr.left) / sr.width : 0.5, fy = sr.height ? (ay - sr.top) / sr.height : 0.5;
    zoom = z;
    stage.style.setProperty("--page-w", `${Math.round(w * zoom)}px`);
    // 让锚点下的那一点在缩放后仍停在光标下
    const nr = stage.getBoundingClientRect();
    v.scrollLeft += nr.left + fx * nr.width - ax;
    v.scrollTop += nr.top + fy * nr.height - ay;
    $("#zoom-level").textContent = `${Math.round(zoom * 100)}%`;
    $("#zoom-out").disabled = zoom <= ZMIN + 1e-6;
    $("#zoom-in").disabled = zoom >= ZMAX - 1e-6;
  }

  const stepZoom = (dir, anchor) => {
    const next = dir > 0 ? ZSTEPS.find((s) => s > zoom + 1e-6) : [...ZSTEPS].reverse().find((s) => s < zoom - 1e-6);
    setZoom(next ?? zoom, anchor);
  };

  function initZoom() {
    const v = $("#viewer");
    $("#zoom-in").onclick = () => stepZoom(1);
    $("#zoom-out").onclick = () => stepZoom(-1);
    $("#zoom-level").onclick = () => setZoom(1);
    $("#zoom-fit").onclick = () => setZoom(1);
    initPan();
    // 鼠标 ⌘/Ctrl + 滚轮；Chrome、Firefox 的触控板捏合也以 ctrlKey 滚轮事件送达。普通滚轮保持平移
    v.addEventListener("wheel", (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      setZoom(zoom * Math.exp(-dy * 0.0015), { x: e.clientX, y: e.clientY });
    }, { passive: false });
    // Safari 的触控板捏合
    let g0 = 1;
    v.addEventListener("gesturestart", (e) => { e.preventDefault(); g0 = zoom; });
    v.addEventListener("gesturechange", (e) => { e.preventDefault(); setZoom(g0 * e.scale, { x: e.clientX, y: e.clientY }); });
    v.addEventListener("gestureend", (e) => e.preventDefault());
    $("#img-after").addEventListener("load", () => { $("#stage").classList.remove("img-loading"); setZoom(zoom); });
    $("#img-after").addEventListener("error", () => $("#stage").classList.remove("img-loading"));
    new ResizeObserver(() => setZoom(zoom)).observe(v);
  }

  // ---------- 手形拖动：工具条按钮或 H 切换，按住空格临时启用，鼠标中键随时可拖 ----------
  let panOn = false, spaceHeld = false, dragged = false;
  function setPan(on) {
    panOn = on;
    $("#pan-tool").setAttribute("aria-pressed", String(on));
    syncPanClass();
  }
  const syncPanClass = () => $("#viewer").classList.toggle("pan", panOn || spaceHeld);
  function initPan() {
    const v = $("#viewer");
    $("#pan-tool").onclick = () => setPan(!panOn);
    let start = null;
    v.addEventListener("pointerdown", (e) => {
      const middle = e.button === 1;
      if (!(middle || (e.button === 0 && (panOn || spaceHeld)))) return;
      e.preventDefault();
      start = { x: e.clientX, y: e.clientY, sl: v.scrollLeft, st: v.scrollTop, id: e.pointerId };
      dragged = false;
      v.setPointerCapture(e.pointerId);
      v.classList.add("panning");
    });
    v.addEventListener("pointermove", (e) => {
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x, dy = e.clientY - start.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) dragged = true;
      v.scrollLeft = start.sl - dx;
      v.scrollTop = start.st - dy;
    });
    const end = (e) => {
      if (!start || e.pointerId !== start.id) return;
      start = null;
      v.classList.remove("panning");
    };
    v.addEventListener("pointerup", end);
    v.addEventListener("pointercancel", end);
    v.addEventListener("auxclick", (e) => { if (e.button === 1) e.preventDefault(); });
    const typing = () => /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
    window.addEventListener("keydown", (e) => {
      if (!report || $("#result").hidden || typing() || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.code === "Space") { e.preventDefault(); if (!spaceHeld) { spaceHeld = true; syncPanClass(); } }
      if (e.key === "h" || e.key === "H") setPan(!panOn);
    });
    window.addEventListener("keyup", (e) => { if (e.code === "Space") { spaceHeld = false; syncPanClass(); } });
    window.addEventListener("blur", () => { spaceHeld = false; syncPanClass(); });
  }
  // 拖动画布时不把松手当成点击（否则会移动对比分隔线）
  const panClick = () => panOn || spaceHeld || dragged;

  // 遮盖统计可收起：电脑上收成窄条、画布变宽；单栏布局下收成一行
  function initSummaryToggle() {
    const btn = $("#sum-toggle");
    const apply = (off) => {
      $("#summary").classList.toggle("off", off);
      $(".viewer-wrap").classList.toggle("sum-off", off);
      $("#sum-body").hidden = off;
      const label = off ? "展开遮盖统计" : "收起遮盖统计";
      btn.setAttribute("aria-expanded", String(!off));
      btn.setAttribute("aria-label", label);
      if (btn.dataset.tip != null) btn.dataset.tip = label; else btn.title = label;
    };
    state.sumCollapsed ??= true;  // 默认收起，用户展开过就记住
    apply(state.sumCollapsed);
    btn.addEventListener("click", () => { state.sumCollapsed = !state.sumCollapsed; save(); apply(state.sumCollapsed); });
  }

  function initHandle() {
    const stage = $("#stage"), handle = $("#handle"), clip = $("#before-clip");
    let pct = 50, pending = null;
    // 左侧原件、右侧脱敏后；分隔线位置写进 --split，遮盖框只显示在右侧。拖动时每帧只重绘一次
    const apply = () => {
      pending = null;
      handle.style.left = `${pct}%`;
      clip.style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
      stage.style.setProperty("--split", `${pct}%`);
      handle.setAttribute("aria-valuenow", Math.round(pct));
    };
    const setPct = (v) => {
      pct = Math.min(100, Math.max(0, v));
      if (pending == null) pending = requestAnimationFrame(apply);
    };
    let rect = null;
    const fromEvent = (e) => { const r = rect || stage.getBoundingClientRect(); setPct(((e.clientX - r.left) / r.width) * 100); };
    handle.addEventListener("pointerdown", (e) => { e.preventDefault(); rect = stage.getBoundingClientRect(); try { handle.setPointerCapture(e.pointerId); } catch { /* 指针已松开 */ } fromEvent(e); });
    handle.addEventListener("pointermove", (e) => { if (handle.hasPointerCapture(e.pointerId)) fromEvent(e); });
    const release = () => { rect = null; };
    handle.addEventListener("pointerup", release);
    handle.addEventListener("pointercancel", release);
    handle.addEventListener("keydown", (e) => { if (e.key === "ArrowLeft") setPct(pct - 5); if (e.key === "ArrowRight") setPct(pct + 5); });
    stage.addEventListener("click", (e) => { if (panClick()) { dragged = false; return; } if (state.view === "compare" && e.target !== handle) fromEvent(e); });
    setPct(50);
  }

  // ---------- 历史 ----------
  async function refreshHistory() {
    try {
      const all = await (await api("/v1/jobs?limit=100")).json();
      if (all.length < 100) jobNames.keepOnly(all.map((j) => j.id));  // 服务端已删除或过期的任务，本地记的文件名也清掉
      const list = all.slice(0, 12);
      const ul = $("#history"); ul.replaceChildren(); ul.setAttribute("aria-busy", "false");
      $("#history-wrap").hidden = !list.length;
      const ST = { succeeded: "完成", failed: "失败", running: "处理中", queued: "排队" };
      for (const j of list) {
        const total = j.summary ? Object.values(j.summary.counts || {}).reduce((a, b) => a + b, 0) : null;
        const when = new Date(j.created * 1000).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
        ul.append(el("li", {},
          el("span", { class: "when num", text: when }),
          el("div", { class: "hist-main" },
            el("span", { class: "hist-name", text: jobNames.get(j.id) || `任务 ${j.id.slice(4, 12)}`, title: jobNames.get(j.id) || "文件名只保存在提交它的浏览器里" }),
            el("span", { class: "hist-meta", text: `${(j.input_ext || "").toUpperCase()} · ${j.pages ?? "?"} 页${total != null ? ` · 遮盖 ${total} 处` : ""}` })),
          el("span", { class: `status ${j.status}`, text: ST[j.status] || j.status }),
          j.status === "succeeded" ? el("button", { type: "button", onclick: () => openJob(j.id, j.input_ext) }, icon("eye"), "查看") : el("span")));
      }
    } catch (e) {
      $("#history").replaceChildren(); $("#history").setAttribute("aria-busy", "false"); $("#history-wrap").hidden = true;
      if (e.status === 401) toast("需要 API Key，请点右上角设置");
    }
  }

  async function openJob(id, ext) {
    job = { id, name: jobNames.get(id) || `任务 ${id.slice(4, 12)}`, kind: (ext || "").toUpperCase() };
    clearBlobs(); beforePage = 0; typeFilter = null;
    showJob();
    showResultSkeleton();
    // 直接滚到任务面板（手机上它在设置区下面，滚到页顶看到的是设置）；先看到骨架，结果到了原地替换
    $("#job").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    const j = await (await api(`/v1/jobs/${id}`)).json();
    await loadResult(j);
  }

  // ---------- 启动 ----------
  // 上传入口：在请求任何数据之前绑定（没有 API Key、目录还没加载时也要有反应）。
  // “选择文件”是原生 <label for="file">，由浏览器直接打开文件选择器，手机浏览器不会拦截
  const DOC_ACCEPT = ".pdf,.doc,.docx,.wps,.rtf,.odt,.xls,.xlsx,.et,.ods,.ppt,.pptx,.dps,.odp,.md,.markdown,.txt,"
    + "application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown";
  function initUpload() {
    const drop = $("#drop");
    const take = (f) => {
      if (!f) return;
      if (!catalog) { openKeyDialog(store.get(KEY_KEY, "") ? "API Key 不正确，请重新填写后再上传。" : "此服务需要 API Key，填写后再上传。"); return; }
      upload(f);
    };
    $("#pick").addEventListener("click", (e) => e.stopPropagation());  // label 自己会打开选择器，不再冒泡到拖放区
    // “照片/图片”：底部菜单给出“拍照”“从相册选择”两个明确的入口（各用一个文件框，不依赖浏览器默认行为）
    const sheet = $("#dlg-photo");
    $("#pick-img").addEventListener("click", (e) => { e.stopPropagation(); sheet.returnValue = ""; sheet.showModal(); });
    sheet.addEventListener("click", (e) => { if (e.target === sheet) sheet.close(); });  // 点菜单外的遮罩收起
    for (const lab of sheet.querySelectorAll(".sheet-item")) {
      lab.addEventListener("click", () => setTimeout(() => sheet.close(), 0));  // 文件选择器打开后收起菜单
      lab.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); lab.click(); } });
    }
    for (const id of ["#file-cam", "#file-img"]) $(id).onchange = (e) => { take(e.target.files[0]); e.target.value = ""; };
    // 手机、平板：可选类型里有图片时，系统先弹“照片图库 / 拍照”。“选择文件”只收文档，直接进入文件选择器；
    // 照片与拍照走单独的“照片/图片”按钮
    if (matchMedia("(pointer: coarse)").matches) $("#file").accept = DOC_ACCEPT;
    $("#pick").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickFile(); } });
    // 文件框被按钮“点击”时，这次点击会冒泡到拖放区：不能再打开一次“选择文件”，否则后打开的会顶掉先打开的
    drop.onclick = (e) => { if (!e.target.closest("#pick, #pick-img, input[type=file]")) pickFile(); };
    drop.onkeydown = (e) => { if (e.target === drop && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pickFile(); } };
    $("#file").onchange = (e) => { take(e.target.files[0]); e.target.value = ""; };
    ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => take(e.dataTransfer.files[0]));
    // 在页面任何位置放下文件都可以
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => { e.preventDefault(); if (!$("#drop").hidden) return; take(e.dataTransfer.files[0]); });
  }

  // 手机上“复核”与“处理新文件、删除结果、下载脱敏文件”排成两行两列；宽屏放回查看栏
  function placeReviewButton() {
    const phone = matchMedia("(max-width: 640px)");
    const btn = $("#btn-review"), actions = $(".job-actions"), bar = $(".viewer-bar"), pager = bar.querySelector(".pager");
    const place = () => { if (phone.matches) actions.append(btn); else bar.insertBefore(btn, pager); };
    phone.addEventListener("change", place);
    place();
  }

  async function init() {
    UI.initTooltips();
    placeReviewButton();
    initKeyDialog();
    initUpload();
    UI.makeSelect($("#opt-retention"));
    // 标题后显示服务版本（/v1/health 不需要 API Key）
    fetch("/v1/health").then((r) => r.json()).then((h) => {
      const v = $("#brand-ver"); v.textContent = `v${h.version}`; v.hidden = false; exportEnabled = !!h.export;
      if (h.max_upload_mb) { maxUploadMB = h.max_upload_mb; $("#max-mb").textContent = maxUploadMB; }
    }).catch(() => {});
    try {
      catalog = await (await api("/v1/catalog")).json();
    } catch (e) {
      if (e.status === 401) {
        openKeyDialog(store.get(KEY_KEY, "") ? "API Key 不正确，请重新填写。" : "此服务需要 API Key，请向管理员索取。");
        toast(store.get(KEY_KEY, "") ? "API Key 不正确" : "需要 API Key");
      } else {
        toast("无法连接服务");
      }
      clearSkeletons();
      return;
    }
    // 填了 Key 且服务接受：右上角按钮显示“已连接”
    if (store.get(KEY_KEY, "")) {
      $("#btn-key").classList.add("connected");
      $("#btn-key-text").textContent = "已连接";
      $("#btn-key").title = "已填写 API Key，点击更换或退出";
    }
    load();
    renderPresets();
    renderEntities();
    $("#custom-words").value = state.custom || "";
    if (typeof state.verify !== "string") state.verify = state.verify ? "on" : "auto";  // 旧版存的是布尔值
    $("#opt-retention").value = state.retention || "24";
    bindSeg("#seg-mode", "mode");
    bindSeg("#seg-label", "label_text");
    bindSeg("#seg-dpi", "dpi", Number);
    bindSeg("#seg-verify", "verify");
    $("#opt-keep").checked = !!state.keep_source;
    $("#opt-keep").addEventListener("change", (e) => { state.keep_source = e.target.checked; save(); });
    initReview();
    state.view = state.view || "compare";
    bindSeg("#seg-view", "view");
    initHandle();
    initZoom();
    initSummaryToggle();
    refreshHistory();

    $("#custom-words").addEventListener("input", (e) => { state.custom = e.target.value; save(); });
    $("#opt-retention").addEventListener("change", (e) => { state.retention = e.target.value; save(); });
    $("#sel-toggle").onclick = (ev) => {
      if (ev.currentTarget.dataset.all === "true") state.entities.clear();
      else selectable().forEach((c) => state.entities.add(c));
      renderEntities(); save();
    };


    $("#btn-new").onclick = async () => { if (reviewing && dirty && !(await UI.confirmDialog("有未保存的修改，处理新文件后这些修改会丢失。", { title: "放弃未保存的修改？", ok: "放弃修改", cancel: "继续复核", danger: true }))) return; stopReview(); clearTimeout(pollTimer); $("#job").hidden = true; $("#drop").hidden = false; job = null; report = null; };
    // 删除前弹出确认框；默认焦点在“取消”上
    const dlgDel = $("#dlg-delete");
    $("#btn-delete").onclick = () => { if (job?.id) { dlgDel.returnValue = ""; dlgDel.showModal(); } };
    dlgDel.addEventListener("close", async () => {
      if (dlgDel.returnValue !== "ok" || !job?.id) return;
      try { await api(`/v1/jobs/${job.id}`, { method: "DELETE" }); jobNames.drop(job.id); toast("已删除脱敏结果与预览"); } catch (e) { toast(e.message); return; }
      $("#btn-new").click(); refreshHistory();
    });
    $("#pg-prev").onclick = () => gotoPage(page - 1);
    $("#pg-next").onclick = () => gotoPage(page + 1);
    $("#show-boxes").onchange = renderBoxes;
    window.addEventListener("keydown", (e) => {
      if (!report || $("#result").hidden || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
      if (e.key === "ArrowDown" || e.key === "PageDown") { e.preventDefault(); gotoPage(page + 1); }
      if (e.key === "ArrowUp" || e.key === "PageUp") { e.preventDefault(); gotoPage(page - 1); }
      if (e.metaKey || e.ctrlKey || e.altKey) return;  // 不拦截浏览器自身的 ⌘+ / ⌘−
      if (e.key === "+" || e.key === "=") { e.preventDefault(); stepZoom(1); }
      if (e.key === "-" || e.key === "_") { e.preventDefault(); stepZoom(-1); }
      if (e.key === "0") { e.preventDefault(); setZoom(1); }
    });

  }

  // 骨架屏：数据加载失败时清掉，免得一直闪
  function clearSkeletons() {
    for (const id of ["#presets", "#entity-groups", "#history"]) { const n = $(id); n.replaceChildren(); n.setAttribute("aria-busy", "false"); }
    $("#history-wrap").hidden = true;
  }

  // 打开任务：报告加载完之前，结果区先显示轮廓
  function showResultSkeleton() {
    $("#progress").hidden = true;
    const r = $("#result"); r.hidden = false; r.classList.add("loading");
    $("#thumbs").replaceChildren(...Array.from({ length: 5 }, () => el("span", { class: "skel sk-thumb", "aria-hidden": "true" })));
    $("#counts").replaceChildren(...Array.from({ length: 7 }, (_, k) => el("li", { class: "sk-count", "aria-hidden": "true" }, el("span", { class: "skel" }), el("span", { class: `skel sk-line w${[70, 55, 60, 45, 65, 50, 60][k]}` }), el("span", { class: "skel sk-line" }))));
    $("#page-items").replaceChildren();
    $("#boxes").replaceChildren();
    $("#sum-total").textContent = "—"; $("#sum-pages").textContent = "—"; $("#sum-foot").textContent = "";
    for (const id of ["#img-after", "#img-before"]) $(id).removeAttribute("src");
    beforePage = 0;
    $("#stage").classList.add("img-loading");
  }

  // API Key 对话框：在请求任何数据之前绑定——没有 Key 时页面其余部分加载不出来，这个按钮必须照样能用
  const KEY_HINT = "服务设置了 API Key 时才需填写，只存本浏览器。";
  function openKeyDialog(hint) {
    const dlg = $("#dlg-key");
    if (dlg.open) return;
    $("#key-hint").textContent = hint || KEY_HINT;
    $("#key-input").value = store.get(KEY_KEY, "");
    $("#key-clear").hidden = !store.get(KEY_KEY, "");
    dlg.returnValue = "";
    dlg.showModal();
    $("#key-input").focus();
  }
  function initKeyDialog() {
    const dlg = $("#dlg-key");
    $("#btn-key").onclick = () => openKeyDialog();
    // 表单里第一个按钮是“取消”：输入框里按回车时按保存处理
    $("#key-input").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); dlg.close("ok"); } });
    dlg.addEventListener("close", () => {
      if (dlg.returnValue === "ok") { store.set(KEY_KEY, $("#key-input").value.trim()); location.reload(); }
      if (dlg.returnValue === "clear") { store.set(KEY_KEY, ""); location.reload(); }  // 退出：清除本浏览器保存的 Key
    });
  }

  init();
})();
