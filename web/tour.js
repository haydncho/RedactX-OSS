/* 锐消 RedactX — 使用向导：第一次打开控制台时自动出现，之后点顶栏“使用向导”随时再看。
   聚光框圈出页面上的真实区域（设置面板、上传区、最近任务……），旁边一张液态玻璃卡片讲解。
   用原生 <dialog> 模态打开：页面其余部分不可操作，Esc 关闭，← → 翻步；关闭后焦点回到打开前的位置。 */
(() => {
  "use strict";
  const { $, el, icon, store } = window.DOM;  // web/dom.js
  const DONE_KEY = "redactx.tour.v1";       // 看过（或关掉）就不再自动弹出
  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const coarse = () => matchMedia("(pointer: coarse)").matches;
  const shown = (n) => !!n && n.getClientRects().length > 0;  // hidden、display:none 的元素没有矩形
  const val = (v) => (typeof v === "function" ? v() : v);
  const panel = (sel) => () => $(sel)?.closest(".panel");
  // 动画曲线与 tour.css 的 --tour-spring、--tour-soft 相同；不支持 linear() 的浏览器退回近似的贝塞尔曲线
  const LINEAR = CSS.supports?.("transition-timing-function", "linear(0, 1)");
  const SPRING = LINEAR ? "linear(0, .054, .185, .357, .538, .708, .853, .967, 1.047, 1.097, 1.122, 1.126, 1.116, 1.098, 1.075, 1.053, 1.032, 1.015, 1.001, .992, .987, .984, .984, .986, .988, .991, .994, .997, 1)" : "cubic-bezier(.34, 1.4, .64, 1)";
  const SOFT = LINEAR ? "linear(0, .06, .201, .373, .546, .699, .824, .918, .984, 1.024, 1.046, 1.054, 1.053, 1.046, 1.037, 1.027, 1.019, 1.011, 1.006, 1.002, .999, .998, .997, .997, 1)" : "cubic-bezier(.3, 1.2, .5, 1)";
  const EASE = "cubic-bezier(.2, .8, .2, 1)";

  const hasDrop = () => shown($("#drop"));
  // target：要圈出的元素（没有则卡片居中）；optional：目标不在页面上时跳过这一步；place：卡片优先放在目标哪一侧
  const STEPS = [
    { art: "welcome", eyebrow: "Welcome", title: "欢迎使用锐消 RedactX",
      body: "病案等文档的本地脱敏工具：自动找出姓名、证件号、电话、签名、印章等内容，按你选的样式打码，全部在这台机器上完成。花一分钟认识一下各个区域。" },
    { target: panel("#presets"), badge: "layers", eyebrow: "Preset", title: "先选一个场景预设",
      body: "按用途一键套用整套打码样式：病案审核用浅色标签，看得出删了什么；对外公开用背景色擦除；打印归档用斜线花纹；也可以全部用黑块。" },
    { target: panel("#entity-groups"), badge: "shield", eyebrow: "Fields", title: "勾选要遮盖的内容",
      body: "人员、机构、证件与号码、医疗标识、手写与印章、图像，按类别分组。每一类都能单独换打码样式，右上角一键全选。日期一律不遮盖。" },
    { target: panel("#custom-words"), badge: "tag", eyebrow: "Custom", title: "补充自定义词",
      body: "院区名称、科室外号这类识别不到的词，每行写一个（至少 2 个字），文中出现的地方都会遮盖。" },
    { target: panel("#seg-mode"), badge: "sliders", eyebrow: "Options", title: "按需调整处理选项",
      body: "“严格”宁可多遮；“出厂自检”对输出再识别一遍、补打遗漏；想在复核时删框、改框，打开“保留原件以便复核”。设置自动记在本浏览器里。" },
    { target: () => (hasDrop() ? $("#drop") : $("#btn-new")), badge: "upload", eyebrow: "Upload",
      title: () => (hasDrop() ? "把文档交给它" : "处理下一个文件"),
      body: () => (!hasDrop() ? "点“处理新文件”回到上传区，再选择或拖入下一个文档。文件只在本机处理，不联网。"
        : coarse() ? "点“选择文件”上传 PDF、Word、Excel 等文档，或用“照片/图片”直接拍下病案。文件只在本机处理，不联网。"
        : "把 PDF、图片、Word、WPS、Excel、PPT 等文档拖到这里（拖到页面任意位置也行），或点“选择文件”。文件只在本机处理，不联网。") },
    { art: "compare", eyebrow: "Result", title: "对比、复核、下载",
      body: "处理完成后，拖动中间的玻璃分隔线对比原件与脱敏后；发现遗漏就点“复核”补画框，保存后重新打码；确认无误再下载。" },
    { target: () => $("#history-wrap"), optional: true, badge: "clock", eyebrow: "Recent", title: "最近任务",
      body: "处理过的文件都列在这里，点“查看”随时重新打开；结果按“结果保留”的时长自动清除，也可以手动删除。" },
    { target: () => $("#btn-tour"), place: "bottom", badge: "compass", eyebrow: "All set", title: "随时回来看看",
      body: "想再看一遍，点右上角的“使用向导”。API Key 与深浅主题也在右上角。祝使用顺利！" },
  ];

  // 插画：只有装饰作用，读屏跳过
  const ART = {
    welcome: () => `
      <span class="ta-blob b1"></span><span class="ta-blob b2"></span><span class="ta-blob b3"></span>
      <div class="ta-sheet">
        <span class="ta-row"><i class="ln" style="width:62%"></i></span>
        <span class="ta-row"><i class="ln" style="width:22%"></i><i class="rv" data-l="姓名" style="--d:.79s;width:34%"></i></span>
        <span class="ta-row"><i class="ln" style="width:26%"></i><i class="rv amber" data-l="身份证号" style="--d:1.14s;width:52%"></i></span>
        <span class="ta-row"><i class="ln" style="width:88%"></i></span>
        <span class="ta-row"><i class="ln" style="width:22%"></i><i class="rv pink" data-l="电话" style="--d:1.84s;width:40%"></i></span>
        <span class="ta-row"><i class="ln" style="width:42%"></i><i class="rv hatch" style="--d:2.19s;width:30%"></i></span>
        <span class="ta-beam"></span>
      </div>
      <span class="ta-bub" style="--s:18px;left:16%;top:58%;--t:4.6s"></span>
      <span class="ta-bub" style="--s:10px;left:24%;top:22%;--t:3.8s;--dl:-1.2s"></span>
      <span class="ta-bub" style="--s:24px;right:13%;top:52%;--t:5.4s;--dl:-2s"></span>
      <span class="ta-bub" style="--s:8px;right:22%;top:16%;--t:4.2s;--dl:-.6s"></span>`,
    compare: () => {
      const rows = () => `
        <span class="ta-row"><i style="width:58%"></i></span>
        <span class="ta-row"><i class="k" style="width:18%"></i><i class="p" style="width:30%"></i></span>
        <span class="ta-row"><i class="k" style="width:24%"></i><i class="p amber" style="width:48%"></i></span>
        <span class="ta-row"><i style="width:86%"></i></span>
        <span class="ta-row"><i class="k" style="width:18%"></i><i class="p pink" style="width:38%"></i></span>`;
      return `
        <div class="ta-page">
          <div class="ta-layer ta-after">${rows()}</div>
          <div class="ta-before"><div class="ta-layer">${rows()}</div></div>
          <span class="ta-handle"><i></i></span>
          <span class="ta-tag l">原件</span><span class="ta-tag r">脱敏后</span>
        </div>
        <div class="ta-chips">
          <span><svg class="ic"><use href="#i-split"/></svg>对比</span>
          <span style="--dl:-4s"><svg class="ic"><use href="#i-edit"/></svg>复核</span>
          <span style="--dl:-2s"><svg class="ic"><use href="#i-download"/></svg>下载</span>
        </div>`;
    },
  };

  let dlg, spot, blobs, card, content, artBox, badge, eyebrow, titleEl, bodyEl, dots, worm, countEl, prevBtn, nextBtn;
  let list = [], idx = 0, cur = null, clips = [], cardH = 0, sizing = false, grow = null, last = "", raf = 0, gliding = 0, seq = 0, leaving = false;

  // 动画：减少动态效果时时长为 0（直接到终点）
  const animate = (node, frames, duration, easing = EASE, fill = "none") => node.animate(frames, { duration: calm() ? 0 : duration, easing, fill });

  function build() {
    if (dlg) return;
    spot = el("div", { class: "tour-spot none", "aria-hidden": "true" }, el("div", { class: "tour-ring" }));
    blobs = el("div", { class: "tour-blobs", "aria-hidden": "true" }, el("i"), el("i"), el("i"), el("i"));
    artBox = el("div", { class: "tour-art", "aria-hidden": "true" });
    badge = el("span", { class: "tour-badge", "aria-hidden": "true" });
    eyebrow = el("span", { class: "tour-eyebrow" });
    countEl = el("span", { class: "tour-count num" });
    titleEl = el("h2", { class: "tour-title", id: "tour-title" });
    bodyEl = el("p", { class: "tour-body", id: "tour-body" });
    content = el("div", { class: "tour-content" }, artBox,
      el("div", { class: "tour-text", "aria-live": "polite" }, el("div", { class: "tour-head" }, badge, el("div", {}, el("div", { class: "tour-meta" }, eyebrow, countEl), titleEl)), bodyEl));
    worm = el("b", { class: "tour-worm" });
    dots = el("div", { class: "tour-dots", "aria-hidden": "true" });
    prevBtn = el("button", { type: "button", class: "tour-btn ghost", onclick: () => (idx === 0 ? close() : step(-1)) });
    nextBtn = el("button", { type: "button", class: "tour-btn go", onclick: () => step(1) });
    card = el("section", { class: "tour-card" },
      el("button", { type: "button", class: "tour-x", "aria-label": "关闭向导", onclick: () => close() }, icon("x")),
      content,
      el("div", { class: "tour-foot" }, dots, prevBtn, nextBtn));
    dlg = el("dialog", { class: "tour", "aria-labelledby": "tour-title", "aria-describedby": "tour-body" }, spot, blobs, card);
    document.body.append(dlg);

    dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });  // Esc：先播放收起动画再关
    dlg.addEventListener("close", cleanup);
    // 翻步快捷键；向导里的按键一律不再传给页面（页面上有翻页、缩放、复核等全局快捷键）
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" && !e.altKey && !e.metaKey && !e.ctrlKey) { e.preventDefault(); step(1); }
      else if (e.key === "ArrowLeft" && !e.altKey && !e.metaKey && !e.ctrlKey) { e.preventDefault(); if (idx > 0) step(-1); }
      e.stopPropagation();
    });
    // 点卡片以外（遮罩、被圈出的区域）：卡片像果冻一样晃一下，提示用卡片上的按钮
    dlg.addEventListener("click", (e) => { if (!card.contains(e.target)) jelly(); });
    // 镜面反光跟着指针走
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      card.style.setProperty("--tour-mx", `${Math.round(e.clientX - r.left)}px`);
      card.style.setProperty("--tour-my", `${Math.round(e.clientY - r.top)}px`);
    });
    card.addEventListener("pointerleave", () => { card.style.removeProperty("--tour-mx"); card.style.removeProperty("--tour-my"); });
    // 改窗口大小（含手机横竖屏切换）后目标被挤出视口：重新滚进来
    let resizeT = 0;
    window.addEventListener("resize", () => {
      clearTimeout(resizeT);
      resizeT = setTimeout(() => { if (dlg.open && cur && !spotRect(cur).seen) reveal(cur, true); }, 200);
    });
  }

  // 当前要走的步骤：可选步骤的目标不在页面上（如还没有任何任务时的“最近任务”）就跳过
  const activeSteps = () => STEPS.filter((s) => !s.optional || shown(val(s.target)));

  function open() {
    build();
    if (dlg.open || leaving) return;
    for (const n of [card, content, spot, blobs]) n.getAnimations().forEach((a) => a.cancel());
    list = activeSteps(); idx = 0; last = ""; seq++; grow = null; sizing = false;
    dots.className = "tour-dots";
    dots.replaceChildren(...list.map(() => el("i")), worm);
    dlg.showModal();
    setCur(null);
    fill(list[0]);
    measure();
    layout();
    // 遮罩淡入，光斑从中心晕开，卡片像一滴水落下来：先扁后圆、略微回弹
    animate(spot, [{ opacity: 0 }, { opacity: 1 }], 360, "ease-out");
    animate(blobs, [{ opacity: 0, transform: "scale(.5)" }, { opacity: 1, transform: "none" }], 900, SOFT);
    animate(card, [
      { opacity: 0, transform: "translateY(26px) scale(.86, .8)", borderRadius: "48px" },
      { opacity: 1, offset: .35 },
      { transform: "none", borderRadius: "24px" },
    ], 760, SPRING);
    nextBtn.focus();
    loop();
  }

  function cleanup() {
    cancelAnimationFrame(raf); raf = 0;
    clearTimeout(gliding); gliding = 0;
    dlg.classList.remove("glide");
    setCur(null);
    document.querySelectorAll(".tour-target").forEach((n) => n.classList.remove("tour-target"));
    leaving = false;
    store.set(DONE_KEY, 1);
  }

  // 关闭：卡片收起、遮罩淡出后再真正关闭；celebrate 时从按钮迸出一捧玻璃泡
  function close(celebrate = false) {
    if (!dlg?.open || leaving) return;
    leaving = true; seq++;
    const b = nextBtn.getBoundingClientRect();
    const d = calm() ? 0 : 260;
    animate(card, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: celebrate ? "scale(.82)" : "translateY(10px) scale(.96)" }], d, "ease-in", "forwards");
    animate(spot, [{ opacity: 1 }, { opacity: 0 }], d, "ease-in", "forwards");
    animate(blobs, [{ opacity: 1 }, { opacity: 0, transform: "scale(.6)" }], d, "ease-in", "forwards");
    setTimeout(() => {
      if (dlg.open) dlg.close();
      if (celebrate) burst(b.left + b.width / 2, b.top + b.height / 2);
    }, d);
  }

  function step(dir) {
    if (leaving) return;
    const n = idx + dir;
    if (n < 0) return;
    if (n >= list.length) { close(true); return; }
    go(n, dir);
  }

  async function go(n, dir) {
    const token = ++seq;
    idx = n;
    const s = list[n];
    const t0 = val(s.target);
    const t = shown(t0) ? t0 : null;
    moveWorm(dir);
    // 1) 旧内容向一侧滑出、变模糊；同时把下一个目标滚进视野
    const out = animate(content, [{ opacity: 1, transform: "none", filter: "blur(0)" }, { opacity: 0, transform: `translateX(${-16 * dir}px)`, filter: "blur(5px)" }], 130, "ease-in", "forwards");
    const scroll = t && !inView(t);
    t?.classList.add("tour-target");
    if (scroll) reveal(t);
    await Promise.all([out.finished.catch(() => {}), scroll ? settle(t) : null]);
    if (token !== seq) return;
    // 2) 换内容，量出新高度；卡片与聚光框按弹簧曲线移到新位置，卡片高度从当前高度弹到新高度
    const h0 = card.offsetHeight;
    grow?.cancel(); sizing = false;
    fill(s);
    setCur(t);
    measure();
    glide();
    layout();
    sizing = true;
    const g = grow = animate(card, [{ height: `${h0}px` }, { height: `${cardH}px` }], 520, SOFT);
    g.finished.then(() => { if (grow === g) sizing = false; }, () => {});
    // 连点时前几次的滑出动画（停在透明）也要一并取消，否则新内容滑入结束后又被它盖成透明
    content.getAnimations().forEach((a) => a.cancel());
    animate(content, [{ opacity: 0, transform: `translateX(${18 * dir}px)`, filter: "blur(5px)" }, { opacity: 1, transform: "none", filter: "blur(0)" }], 340, EASE);
    if (t) setTimeout(() => token === seq && ping(), 420);
  }

  function fill(s) {
    const first = idx === 0, end = idx === list.length - 1;
    artBox.hidden = !s.art;
    if (s.art) { artBox.className = `tour-art ta-${s.art}`; artBox.innerHTML = ART[s.art](); }
    badge.hidden = !s.badge;
    if (s.badge) badge.replaceChildren(icon(s.badge));
    eyebrow.textContent = s.eyebrow;
    titleEl.textContent = val(s.title);
    bodyEl.textContent = val(s.body);
    prevBtn.replaceChildren(...(first ? ["跳过"] : [icon("chev-left"), "上一步"]));
    nextBtn.replaceChildren(first ? "开始导览" : end ? "开始使用" : "下一步", icon(end ? "check" : "chev-right"));
    countEl.textContent = `${idx + 1} / ${list.length}`;
    if (first) moveWorm(0);
  }

  // 进度水滴：前进时右端先伸过去、左端随后收拢；后退反过来
  function moveWorm(dir) {
    const step_ = 14;  // 点 7px + 间距 7px
    dots.classList.toggle("fwd", dir > 0);
    dots.classList.toggle("back", dir < 0);
    worm.style.left = `${idx * step_}px`;
    worm.style.right = `${(list.length - 1 - idx) * step_}px`;
  }

  function setCur(t) {
    if (cur && cur !== t) cur.classList.remove("tour-target");
    cur = t;
    // 目标所在的滚动容器（如电脑上设置栏自己滚动）：聚光框只圈出容器里看得见的部分
    clips = [];
    for (let p = t?.parentElement; p && p !== document.body; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflowX !== "visible" || cs.overflowY !== "visible") clips.push(p);
    }
  }

  function reveal(t, instant = false) {
    if (t.closest(".topbar")) return;  // 顶栏固定在上面，不用滚
    const single = document.documentElement.clientWidth <= 1100;  // 单栏布局（平板、手机）
    t.scrollIntoView({ block: single ? "start" : "nearest", inline: "nearest", behavior: instant || calm() ? "auto" : "smooth" });
  }

  // 目标已经整个露在视口（以及它所在的滚动容器）里：不用滚，也不用等
  function inView(t) {
    if (t.closest(".topbar")) return true;
    const r = t.getBoundingClientRect();
    let top = $(".topbar").getBoundingClientRect().bottom, bottom = dlg.clientHeight;
    for (let p = t.parentElement; p && p !== document.body; p = p.parentElement) {
      if (getComputedStyle(p).overflowY === "visible") continue;
      const q = p.getBoundingClientRect();
      top = Math.max(top, q.top); bottom = Math.min(bottom, q.bottom);
    }
    return r.top >= top - 1 && r.bottom <= bottom + 1;
  }

  // 等滚动停下：目标位置连续 5 帧不变（最多等 1 秒）
  function settle(t) {
    return new Promise((done) => {
      let prev = "", still = 0, n = 0;
      const tick = () => {
        const r = t.getBoundingClientRect(), k = `${Math.round(r.left)},${Math.round(r.top)}`;
        still = k === prev ? still + 1 : 0; prev = k;
        if (still >= 5 || ++n > 60 || !dlg.open) return done();
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }

  function spotRect(t) {
    const vw = dlg.clientWidth, vh = dlg.clientHeight;
    const r = t.getBoundingClientRect();
    let { left, top, right, bottom } = r;
    for (const p of clips) {
      const q = p.getBoundingClientRect();
      left = Math.max(left, q.left); top = Math.max(top, q.top); right = Math.min(right, q.right); bottom = Math.min(bottom, q.bottom);
    }
    const bar = t.closest(".topbar") ? 0 : $(".topbar").getBoundingClientRect().bottom;
    left = Math.max(left, 0); top = Math.max(top, bar); right = Math.min(right, vw); bottom = Math.min(bottom, vh);
    const pad = 6, w = Math.max(0, right - left) + 2 * pad, h = Math.max(0, bottom - top) + 2 * pad;
    const rad = parseFloat(getComputedStyle(t).borderTopLeftRadius) || 0;
    const seen = bottom - top > 4 && right - left > 4;  // 目标此刻有没有露在视口里
    return { x: Math.round(left - pad), y: Math.round(top - pad), w: Math.round(w), h: Math.round(h), r: Math.round(Math.min(rad + pad, h / 2)), seen };
  }

  // 卡片位置：手机上贴底，贴顶栏下方挡得更少时改贴顶；没有目标时居中；
  // 否则按优先顺序放在目标右、左、下、上，哪边放得下用哪边
  function placeCard(r, w, h, pref) {
    const vw = dlg.clientWidth, vh = dlg.clientHeight, m = 12, gap = 14;
    const top0 = $(".topbar").getBoundingClientRect().bottom + m;
    if (vw <= 640) {
      const safe = parseFloat(getComputedStyle(dlg).paddingBottom) || 0;
      const low = vh - h - m - safe;
      const cover = (y) => (r ? Math.max(0, Math.min(y + h, r.y + r.h) - Math.max(y, r.y)) : 0);
      return { x: m, y: cover(top0) < cover(low) ? top0 : low };
    }
    if (!r) return { x: (vw - w) / 2, y: Math.max(m, (vh - h) / 2) };
    const cy = (y) => Math.max(top0, Math.min(y, vh - h - m));
    const cx = (x) => Math.max(m, Math.min(x, vw - w - m));
    const at = {
      right: () => r.x + r.w + gap + w <= vw - m && { x: r.x + r.w + gap, y: cy(r.y) },
      left: () => r.x - gap - w >= m && { x: r.x - gap - w, y: cy(r.y) },
      bottom: () => r.y + r.h + gap + h <= vh - m && { x: cx(r.x + r.w / 2 - w / 2), y: r.y + r.h + gap },
      top: () => r.y - gap - h >= top0 && { x: cx(r.x + r.w / 2 - w / 2), y: r.y - gap - h },
    };
    for (const k of pref === "bottom" ? ["bottom", "left", "right", "top"] : ["right", "left", "bottom", "top"]) {
      const p = at[k]();
      if (p) return p;
    }
    return { x: cx((vw - w) / 2), y: vh - h - 24 };  // 目标太大，哪边都放不下：浮在底部
  }

  // 卡片宽度与自然高度（高度动画进行中时沿用上次量的值）
  function measure() {
    const vw = dlg.clientWidth;
    const w = vw <= 640 ? vw - 24 : Math.min(380, vw - 24);
    if (card.style.width !== `${w}px`) card.style.width = `${w}px`;
    if (!sizing) cardH = card.offsetHeight;
    return w;
  }

  function layout() {
    const w = measure();
    const vw = dlg.clientWidth, vh = dlg.clientHeight;
    const r = cur ? spotRect(cur) : null;
    // 目标暂时不在视口里（如窗口变窄后布局改成单栏）：卡片先居中，且无论如何不出视口
    const p = placeCard(r?.seen ? r : null, w, cardH, list[idx]?.place);
    const x = Math.round(Math.max(12, Math.min(p.x, vw - w - 12))), y = Math.round(Math.max(12, Math.min(p.y, vh - cardH - 12)));
    const key = `${r ? `${r.x},${r.y},${r.w},${r.h},${r.r}` : "-"}|${x},${y},${w},${cardH}`;
    if (key === last) return;
    last = key;
    const g = r || { x: Math.round(vw / 2), y: Math.round(vh / 2), w: 0, h: 0, r: 24 };
    Object.assign(spot.style, { left: `${g.x}px`, top: `${g.y}px`, width: `${g.w}px`, height: `${g.h}px`, borderRadius: `${g.r}px` });
    spot.classList.toggle("none", !r);
    Object.assign(card.style, { left: `${x}px`, top: `${y}px` });
    Object.assign(blobs.style, { left: `${x + 16}px`, top: `${y + 12}px`, width: `${w - 32}px`, height: `${Math.max(0, cardH - 24)}px` });
  }

  // 换步时的一次滑行：打开过渡，结束后关掉，平时（滚动、改窗口大小）位置即时跟随
  function glide() {
    dlg.classList.add("glide");
    clearTimeout(gliding);
    gliding = setTimeout(() => { gliding = 0; dlg.classList.remove("glide"); }, 1100);
  }

  function loop() {
    if (!dlg.open) return;
    if (!gliding && !leaving) layout();
    raf = requestAnimationFrame(loop);
  }

  function ping() {
    if (calm()) return;
    spot.classList.remove("ping");
    void spot.offsetWidth;  // 重新触发动画
    spot.classList.add("ping");
  }

  function jelly() {
    if (calm() || leaving) return;
    animate(card, [
      { transform: "none" }, { transform: "scale(1.04, .95)" }, { transform: "scale(.97, 1.03)" },
      { transform: "scale(1.015, .99)" }, { transform: "none" },
    ], 560, "ease-out");
  }

  // 完成：一捧玻璃泡和打码小标签从按钮迸出，抛起后落下、淡出
  function burst(x, y) {
    if (calm()) return;
    const colors = ["#0070F3", "#7C3AED", "#EC4899", "#F59E0B", "#29BC9B"];
    const layer = el("div", { class: "tour-confetti", "aria-hidden": "true" });
    document.body.append(layer);
    for (let k = 0; k < 30; k++) {
      const s = 7 + Math.random() * 9;
      const p = el("i", { class: k % 3 === 0 ? "bar" : "", style: `--s:${s.toFixed(1)}px;--c:${colors[k % colors.length]};left:${x}px;top:${y}px` });
      layer.append(p);
      const a = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.4, d = 90 + Math.random() * 190;
      const dx = Math.cos(a) * d, dy = Math.sin(a) * d, rot = (Math.random() - 0.5) * 540;
      p.animate([
        { transform: "translate(-50%, -50%) scale(.2)", opacity: 1 },
        { transform: `translate(-50%, -50%) translate(${dx}px, ${dy}px) rotate(${rot / 2}deg) scale(1)`, opacity: 1, offset: 0.5 },
        { transform: `translate(-50%, -50%) translate(${dx * 1.2}px, ${dy + 160}px) rotate(${rot}deg) scale(.5)`, opacity: 0 },
      ], { duration: 1100 + Math.random() * 600, easing: "cubic-bezier(.15, .7, .35, 1)", fill: "forwards" });
    }
    setTimeout(() => layer.remove(), 1900);
  }

  // 第一次打开（没看过、也没关过）时自动出现；有别的对话框开着（如填写 API Key）就不打扰
  function auto() {
    if (store.get(DONE_KEY, 0)) return;
    setTimeout(() => { if (!document.querySelector("dialog[open]")) open(); }, 500);
  }

  $("#btn-tour")?.addEventListener("click", () => open());
  window.Tour = { open, auto };
})();
