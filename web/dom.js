/* 锐消 RedactX 网页共用的小工具：选择器、建元素、图标、提示条、本地存储。应用页与接口文档页共用，先于其他脚本加载。 */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  // 建元素：class、text 特殊处理，on* 绑定事件；值为 null、undefined、false 的属性不设，true 设为空值
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
  // 图标：引用页面里定义的 <symbol id="i-名称">
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
  const toast = (msg, ms = 2600) => {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), ms);
  };
  // 本地存储（JSON）；隐私模式等读写失败时按没有处理
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 隐私模式下忽略 */ } },
  };
  const KEY_KEY = "redactx.apikey";
  window.DOM = { $, el, icon, toast, store, KEY_KEY, apiKey: () => store.get(KEY_KEY, "") };
})();
