/* 锐消 RedactX — 自定义组件（Vercel 风格）：下拉菜单、确认框、悬停提示。
   原生 <select> 保留在页面里存值、派发 change 事件，外观换成按钮 + 浮层菜单；原有代码照常读写 select.value。 */
(function () {
  "use strict";
  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const c of kids) if (c != null) n.append(c);
    return n;
  };
  const chevron = () => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "dd-chev"); s.setAttribute("aria-hidden", "true");
    s.innerHTML = '<path d="M7 10l5 5 5-5"/>';
    return s;
  };
  const check = () => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "dd-check"); s.setAttribute("aria-hidden", "true");
    s.innerHTML = '<path d="M5 12.5l4.5 4.5L19 7.5"/>';
    return s;
  };

  // ---------- 下拉菜单 ----------
  const proto = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  let openMenu = null; // 当前展开的 { sel, menu, trigger }

  function closeMenu(focusTrigger) {
    if (!openMenu) return;
    const { menu, trigger } = openMenu;
    menu.remove();
    trigger.setAttribute("aria-expanded", "false");
    if (focusTrigger) trigger.focus();
    openMenu = null;
  }

  function makeSelect(sel) {
    if (sel._dd) return sel._dd;
    const label = h("span", { class: "dd-label" });
    const trigger = h("button", { type: "button", class: "dd-trigger", "aria-haspopup": "listbox", "aria-expanded": "false" }, label, chevron());
    const aria = sel.getAttribute("aria-label") || (sel.id && document.querySelector(`label[for="${sel.id}"]`)?.textContent.trim());
    if (aria) trigger.setAttribute("aria-label", aria);
    if (sel.id) { trigger.id = `${sel.id}-dd`; document.querySelectorAll(`label[for="${sel.id}"]`).forEach((l) => l.setAttribute("for", trigger.id)); }
    sel.classList.add("dd-native");
    sel.tabIndex = -1;
    sel.setAttribute("aria-hidden", "true");
    sel.after(trigger);

    const sync = () => {
      const o = sel.options[sel.selectedIndex];
      label.textContent = o ? o.textContent : "";
      trigger.disabled = sel.disabled;
      trigger.title = o ? o.textContent : "";
    };
    // 代码里直接改 select.value 时也同步外观
    Object.defineProperty(sel, "value", { configurable: true, get() { return proto.get.call(this); }, set(v) { proto.set.call(this, v); sync(); } });
    new MutationObserver(sync).observe(sel, { childList: true, attributes: true, attributeFilter: ["disabled"] });
    sel.addEventListener("change", sync);

    function open() {
      if (sel.disabled) return;
      closeMenu(false);
      const menu = h("div", { class: "dd-menu", role: "listbox", tabindex: "-1" });
      if (aria) menu.setAttribute("aria-label", aria);
      const items = [...sel.options].map((o, k) => {
        const desc = o.getAttribute("title") || o.dataset.desc;
        const it = h("div", { class: "dd-item", role: "option", "aria-selected": String(k === sel.selectedIndex), "data-k": String(k) }, check(),
          h("span", { class: "dd-text" }, h("span", { text: o.textContent }), desc ? h("small", { text: desc }) : null));
        if (o.disabled) it.setAttribute("aria-disabled", "true");
        it.addEventListener("pointerenter", () => setActive(k));
        it.addEventListener("click", () => pick(k));
        return it;
      });
      menu.append(...items);
      document.body.append(menu);
      const r = trigger.getBoundingClientRect();
      menu.style.minWidth = `${r.width}px`;
      const below = window.innerHeight - r.bottom, mh = Math.min(menu.scrollHeight, 420);
      const up = below < mh + 12 && r.top > below;
      menu.style.left = `${Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)}px`;
      menu.style.top = up ? `${r.top - mh - 6}px` : `${r.bottom + 6}px`;
      menu.style.maxHeight = "420px";
      trigger.setAttribute("aria-expanded", "true");
      let active = Math.max(0, sel.selectedIndex);
      function setActive(k) {
        active = k;
        items.forEach((it, i) => it.classList.toggle("active", i === k));
        items[k]?.scrollIntoView({ block: "nearest" });
      }
      function pick(k) {
        if (sel.options[k]?.disabled) return;
        const changed = k !== sel.selectedIndex;
        proto.set.call(sel, sel.options[k].value);
        sync();
        closeMenu(true);
        if (changed) sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
      let typed = "", typedAt = 0;
      menu.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(items.length - 1, active + 1)); }
        else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(0, active - 1)); }
        else if (e.key === "Home") { e.preventDefault(); setActive(0); }
        else if (e.key === "End") { e.preventDefault(); setActive(items.length - 1); }
        else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(active); }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
        else if (e.key === "Tab") { closeMenu(false); }
        else if (e.key.length === 1) { // 按字跳转
          const now = Date.now(); typed = now - typedAt > 600 ? e.key : typed + e.key; typedAt = now;
          const k = [...sel.options].findIndex((o) => o.textContent.startsWith(typed));
          if (k >= 0) setActive(k);
        }
      });
      openMenu = { sel, menu, trigger };
      setActive(active);
      menu.focus();
    }
    trigger.addEventListener("click", () => (openMenu && openMenu.sel === sel ? closeMenu(true) : open()));
    trigger.addEventListener("keydown", (e) => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(); } });
    sync();
    sel._dd = { trigger, sync };
    return sel._dd;
  }
  document.addEventListener("pointerdown", (e) => { if (openMenu && !openMenu.menu.contains(e.target) && !openMenu.trigger.contains(e.target)) closeMenu(false); }, true);
  window.addEventListener("resize", () => closeMenu(false));
  document.addEventListener("scroll", (e) => { if (openMenu && !openMenu.menu.contains(e.target)) closeMenu(false); }, true);

  // ---------- 确认框 ----------
  function confirmDialog(message, { title = "确认", ok = "确定", cancel = "取消", danger = false } = {}) {
    return new Promise((resolve) => {
      const dlg = h("dialog", { class: danger ? "dlg-danger ui-confirm" : "ui-confirm" },
        h("form", { method: "dialog" },
          h("h3", { text: title }),
          h("p", { class: "hint", text: message }),
          h("div", { class: "dlg-actions" },
            h("button", { class: "btn ghost", value: "cancel", text: cancel }),
            h("button", { class: danger ? "btn danger-solid" : "btn primary", value: "ok", text: ok }))));
      document.body.append(dlg);
      dlg.addEventListener("close", () => { resolve(dlg.returnValue === "ok"); dlg.remove(); });
      dlg.showModal();
      dlg.querySelector('button[value="cancel"]').focus();
    });
  }

  // ---------- 悬停提示：接管 title 属性，换成统一样式的气泡 ----------
  let tip = null, tipFor = null, tipTimer = 0;
  function hideTip() {
    clearTimeout(tipTimer);
    if (tipFor && tipFor.dataset.tip != null && !tipFor.hasAttribute("title")) { /* 保持 data-tip，下次仍可显示 */ }
    tip?.remove(); tip = null; tipFor = null;
  }
  function showTip(el) {
    const text = el.dataset.tip;
    if (!text) return;
    tip = h("div", { class: "ui-tip", role: "tooltip", text });
    document.body.append(tip);
    const r = el.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
    let top = r.bottom + 8;
    if (top + th > window.innerHeight - 8) top = r.top - th - 8;
    tip.style.top = `${Math.max(8, top)}px`;
    tip.style.left = `${Math.min(Math.max(8, r.left + r.width / 2 - tw / 2), window.innerWidth - tw - 8)}px`;
  }
  function claim(el) {
    // 把 title 挪到 data-tip，避免浏览器原生提示框同时出现；之后代码再改 title 也会被接管
    if (el.hasAttribute("title")) { el.dataset.tip = el.getAttribute("title"); el.removeAttribute("title"); }
    return el.dataset.tip ? el : null;
  }
  function onOver(e) {
    const el = e.target.closest?.("[title], [data-tip]");
    if (!el || el === tipFor) return;
    hideTip();
    if (!claim(el)) return;
    tipFor = el;
    tipTimer = setTimeout(() => { if (tipFor === el) showTip(el); }, 350);
  }
  function initTooltips() {
    document.addEventListener("pointerover", onOver);
    document.addEventListener("pointerout", (e) => { if (tipFor && !tipFor.contains(e.relatedTarget)) hideTip(); });
    document.addEventListener("pointerdown", hideTip, true);
    document.addEventListener("scroll", hideTip, true);
    document.addEventListener("focusin", (e) => { const el = e.target.closest?.("[title], [data-tip]"); if (el && e.target.matches(":focus-visible") && claim(el)) { hideTip(); tipFor = el; showTip(el); } });
    document.addEventListener("focusout", hideTip);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  }

  window.UI = { makeSelect, confirmDialog, initTooltips };
})();
