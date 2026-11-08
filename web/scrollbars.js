/* 监听捕获阶段的 scroll，兼容动态生成的预览、列表和下拉菜单。 */
(() => {
  const states = new WeakMap();
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const paint = (scroller, opacity) => scroller.style.setProperty("--scrollbar-thumb",
    `color-mix(in srgb, var(--ink, #171717) ${40 * opacity}%, transparent)`);
  function hide(scroller, state) {
    scroller.removeAttribute("data-scrolling");
    const start = performance.now();
    function fade(now) {
      const progress = reducedMotion.matches ? 1 : Math.min(1, (now - start) / 350);
      if (progress < 1 && scroller.isConnected) {
        paint(scroller, 1 - progress);
        state.frame = requestAnimationFrame(fade);
      } else {
        scroller.style.removeProperty("--scrollbar-thumb");
        states.delete(scroller);
      }
    }
    state.frame = requestAnimationFrame(fade);
  }
  document.addEventListener("scroll", (event) => {
    const scroller = event.target === document ? document.scrollingElement : event.target;
    if (!(scroller instanceof Element)) return;
    const previous = states.get(scroller);
    if (previous) { clearTimeout(previous.timer); cancelAnimationFrame(previous.frame); }
    const state = {};
    states.set(scroller, state);
    scroller.setAttribute("data-scrolling", "");
    paint(scroller, 1);
    state.timer = setTimeout(() => hide(scroller, state), 900);
  }, { capture: true, passive: true });
  document.documentElement.classList.add("auto-scrollbars");
})();
