/* 主题：浅色 / 深色。用户选过就记在本机浏览器；没选过则跟随系统设置。
   在 <head> 里同步执行，页面绘制前就定好主题，避免闪烁。 */
(function () {
  var KEY = "redactx-theme";
  var root = document.documentElement;
  var mq = window.matchMedia("(prefers-color-scheme: dark)");

  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function label(t) {
    var btn = document.getElementById("btn-theme");
    if (!btn) return;
    var text = t === "dark" ? "切换到浅色主题" : "切换到深色主题";
    btn.setAttribute("aria-label", text);
    btn.title = text;
  }
  function apply(t) {
    root.setAttribute("data-theme", t);
    label(t);
  }

  apply(saved() || (mq.matches ? "dark" : "light"));
  mq.addEventListener("change", function (e) {
    if (!saved()) apply(e.matches ? "dark" : "light");
  });

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("btn-theme");
    if (!btn) return;
    label(root.getAttribute("data-theme"));
    btn.addEventListener("click", function () {
      var t = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      apply(t);
      try { localStorage.setItem(KEY, t); } catch (e) { /* 隐私模式下不记忆，仅本次生效 */ }
    });
  });
})();
