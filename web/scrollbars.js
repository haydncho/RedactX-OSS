/* 监听捕获阶段的 scroll，兼容动态生成的预览、列表和下拉菜单。 */
(() => {
  const timers = new WeakMap();
  document.addEventListener("scroll", (event) => {
    const scroller = event.target === document ? document.scrollingElement : event.target;
    if (!(scroller instanceof Element)) return;
    scroller.setAttribute("data-scrolling", "");
    clearTimeout(timers.get(scroller));
    timers.set(scroller, setTimeout(() => {
      scroller.removeAttribute("data-scrolling");
      timers.delete(scroller);
    }, 900));
  }, { capture: true, passive: true });
  document.documentElement.classList.add("auto-scrollbars");
})();
