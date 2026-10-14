// Native fullscreen is opt-in. Unsupported browsers get an honest installation
// path; hiding CSS chrome is not presented as browser fullscreen.
export function initScreenControls() {
  const button = document.getElementById('fullscreen-toggle');
  const help = document.getElementById('fullscreen-help');
  const root = document.documentElement;
  const standalone = matchMedia('(display-mode: standalone)');
  const installedFullscreen = matchMedia('(display-mode: fullscreen)');
  const active = () => document.fullscreenElement || document.webkitFullscreenElement;
  function update() {
    const on = Boolean(active());
    button.textContent = on ? '退出全屏' : '全屏';
    button.setAttribute('aria-label', on ? '退出网页全屏' : '进入网页全屏');
    button.setAttribute('aria-pressed', String(on));
    button.hidden = !on && (standalone.matches || installedFullscreen.matches || navigator.standalone === true);
  }
  function explain(message) {
    document.getElementById('fullscreen-help-copy').textContent = message;
    if (!help.open) help.showModal();
  }
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (active()) {
        if (document.exitFullscreen) await document.exitFullscreen();
        else await document.webkitExitFullscreen();
      } else if (root.requestFullscreen && document.fullscreenEnabled) {
        await root.requestFullscreen({ navigationUI: 'hide' });
      } else if (root.webkitRequestFullscreen && document.webkitFullscreenEnabled) {
        await root.webkitRequestFullscreen();
      } else {
        explain('当前浏览器未开放网页全屏。添加到主屏幕后打开，可以为牌桌留出更多空间。');
      }
    } catch {
      explain('浏览器未能切换全屏。可以关闭此提示后重试，或从主屏幕打开游戏。');
    } finally {
      button.disabled = false;
      update();
    }
  });
  document.addEventListener('fullscreenchange', update);
  document.addEventListener('webkitfullscreenchange', update);
  standalone.addEventListener('change', update);
  installedFullscreen.addEventListener('change', update);
  document.getElementById('fullscreen-help-close').onclick = () => help.close();
  update();

  // Keep infrequent actions reachable without taking permanent table space.
  // Move the original controls so handlers, disabled state and focus survive.
  const compact = matchMedia('(max-width:760px), (max-height:520px)');
  const menu = document.getElementById('match-menu');
  const home = menu.parentElement;
  const dialog = document.getElementById('table-menu-dialog');
  function placeMenu() {
    if (dialog.open) dialog.close();
    (compact.matches ? dialog : home).append(menu);
  }
  document.getElementById('table-menu-open').onclick = () => dialog.showModal();
  document.getElementById('table-menu-close').onclick = () => dialog.close();
  menu.addEventListener('click', event => {
    if (dialog.open && event.target.closest('button:not(:disabled)')) dialog.close();
  }, true);
  compact.addEventListener('change', placeMenu);
  placeMenu();
}
