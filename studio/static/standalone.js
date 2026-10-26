// Standalone only: the home page links straight to a part of the studio with
// ?open=board | library | footage. The studio has no deep links of its own, so
// this presses the same button a person would, once the page has drawn it.
(function () {
  const want = new URLSearchParams(location.search).get("open");
  if (!want) return;
  const press = {
    board: () => document.querySelector('#viewSeg button[data-v="board"]'),
    library: () => document.getElementById("libBtn"),
  }[want];
  if (!press) return;
  let tries = 0;
  const go = () => {
    const el = press();
    if (el && !el.disabled && el.offsetParent !== null) {
      el.click();
      history.replaceState(null, "", location.pathname);   // a reload shouldn't press it again
    } else if (++tries < 40) {
      setTimeout(go, 150);
    }
  };
  window.addEventListener("load", () => setTimeout(go, 300));
})();
