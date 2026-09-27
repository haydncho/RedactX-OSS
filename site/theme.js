// 主题：用户选择优先，否则跟随系统。在 <head> 里同步加载，先于页面渲染写入，避免闪烁
(function () {
  var t = null;
  try { t = localStorage.getItem("redactx-site-theme"); } catch (e) {}
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
  document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("theme").addEventListener("click", function () {
      var n = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", n);
      try { localStorage.setItem("redactx-site-theme", n); } catch (e) {}
    });
  });
})();
