"""Real browser regressions for the desktop-derived table and slow connections."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.artifacts'

# One late auth and one withheld update exercise recovery over real sockets.
# The move is still delivered to the server once; only its first browser update
# is withheld. All protocol/auth/engine validation remains enabled.
FAULTS = """(() => {
  window.uiWire = { actions: 0, syncs: 0, retries: 0 };
  window.WebSocket = new Proxy(window.WebSocket, { construct(Target, args) {
    const ws = Reflect.construct(Target, args);
    window.uiSocket = ws;
    const send = ws.send.bind(ws);
    ws.send = value => {
      const data = JSON.parse(value);
      if (data.type === 'action') window.uiWire.actions++;
      if (data.type === 'sync') window.uiWire.syncs++;
      if (data.type === 'auth' && sessionStorage.getItem('delay-first-auth')) {
        sessionStorage.removeItem('delay-first-auth');
        setTimeout(() => { if (ws.readyState === 1) send(value); }, 900);
      } else send(value);
    };
    let handler;
    Object.defineProperty(ws, 'onmessage', {
      get: () => handler,
      set: callback => {
        handler = callback;
        ws.addEventListener('message', event => {
          const data = JSON.parse(event.data);
          if (data.type === 'retry') window.uiWire.retries++;
          if (data.type === 'patch' && window.holdNextPatch) {
            window.holdNextPatch = false; return;
          }
          callback(event);
        });
      }
    });
    return ws;
  }});
})();"""


def held_count(page):
    return int(page.locator('#trump-count').inner_text().split('/')[0].strip())


def mouse_drag(page, dx, dy):
    bounds = page.locator('.trump-card').first.bounding_box()
    x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=5)
    ghost = page.locator('.drag-ghost').bounding_box()
    assert ghost and ghost['width'] <= 140 and ghost['height'] <= 140, 'Drag feedback must remain card-sized'
    page.mouse.up()


def touch_drag(page, dx, dy):
    bounds = page.locator('.trump-card').first.bounding_box()
    x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
        for step in range(1, 6):
            cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x + dx * step / 5, 'y': y + dy * step / 5}]})
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    finally:
        cdp.detach()


def assert_count(page, expected):
    expect(page.locator('#trump-count')).to_contain_text(f'{expected} /')


def check_usability(url):
    OUT.mkdir(exist_ok=True)
    errors = []
    report = {'viewports': [], 'gestures': [], 'recovery': {}}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            a = browser.new_page(viewport={'width': 1440, 'height': 900}, has_touch=True)
            b = browser.new_page(viewport={'width': 1366, 'height': 768})
            for page in [a, b]:
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
                page.add_init_script(FAULTS)
                page.goto(url)
            config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8-sig'))
            config['game_settings'].update(max_hp=2, initial_trumps_count=8,
                                           round_reward_trumps_count=0, number_card_draw_probability=0)
            config['trump_weights'] = {k: 0 for k, v in config['trump_weights'].items() if isinstance(v, (int, float))}
            config['trump_weights']['Add 1'] = 1
            created = a.request.post(url + '/api/rooms', data={
                'name': '布局测试房主', 'config': config,
                'timer': {'enabled': False, 'settlement_seconds': .2}})
            assert created.ok, created.text()
            host = created.json()
            joined = b.request.post(url + f"/api/rooms/{host['room']}/join", data={'name': '布局测试对手'})
            assert joined.ok, joined.text()
            prefix = urlsplit(url).path.rstrip('/')
            seat_key = prefix + '/noir-seat' if prefix else 'noir-seat'
            for page, seat in [(a, host), (b, joined.json())]:
                page.evaluate('([key, seat]) => sessionStorage.setItem(key, JSON.stringify(seat))',
                              [seat_key, seat])
                if page is a:
                    page.evaluate("sessionStorage.setItem('delay-first-auth', 'yes')")
                page.goto(url + '/?room=' + seat['room'])
                expect(page.locator('#ready')).to_be_enabled(timeout=10000)
            assert a.evaluate('window.uiWire.retries') == 1
            report['recovery']['auth_timeout'] = 'same reserved seat recovered automatically'
            a.locator('#ready').click()
            b.locator('#ready').click()
            expect(a.locator('#game')).to_be_visible()
            expect(a.locator('#stay')).to_be_enabled()
            assert_count(a, 8)

            for width, height in [(1920, 1080), (1440, 900), (1366, 768), (390, 844), (360, 640), (320, 568), (844, 390)]:
                a.set_viewport_size({'width': width, 'height': height})
                a.wait_for_timeout(80)
                geometry = a.evaluate("""() => {
                  const ids = ['opponent', 'my-player', 'trump-hand', 'hit', 'stay', 'use-trump', 'discard-trump'];
                  return {width: innerWidth, height: innerHeight,
                    documentWidth: document.documentElement.scrollWidth,
                    documentHeight: document.documentElement.scrollHeight,
                    bounds: Object.fromEntries(ids.map(id => {
                      const r = document.getElementById(id).getBoundingClientRect();
                      return [id, {x:r.x, y:r.y, right:r.right, bottom:r.bottom, width:r.width, height:r.height}];
                    }))};
                }""")
                assert geometry['documentWidth'] <= width, geometry
                assert geometry['documentHeight'] <= height, geometry
                for name, bounds in geometry['bounds'].items():
                    assert bounds['width'] > 0 and bounds['height'] > 0, (name, geometry)
                    assert bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['right'] <= width + 1 and bounds['bottom'] <= height + 1, (name, geometry)
                report['viewports'].append(geometry)
                if (width, height) in [(1440, 900), (390, 844), (844, 390)]:
                    a.screenshot(path=str(OUT / f'web-ui-{width}x{height}.png'), animations='disabled')

            a.set_viewport_size({'width': 1440, 'height': 900})
            expect(a.locator('.trump-card')).to_have_count(6)
            a.locator('#hand-next').click()
            expect(a.locator('.trump-card')).to_have_count(2)
            assert a.locator('.trump-card').first.get_attribute('data-index') == '6'
            a.locator('#hand-prev').click()
            before = held_count(a)
            mouse_drag(a, 8, -6)
            a.wait_for_timeout(150)
            assert held_count(a) == before, 'A small accidental movement must not play a card'
            mouse_drag(a, -60, 30)
            a.wait_for_timeout(150)
            assert held_count(a) == before, 'Left/down movement must not discard'
            mouse_drag(a, 0, -40)
            assert_count(a, before - 1)
            expect(a.locator('#my-effects .effect')).to_have_count(1)
            expect(a.locator('#stake-label')).to_contain_text('2 / 1')
            report['gestures'].append('mouse: up40px plays without reaching table')
            a.locator('.trump-card').first.click()
            expect(a.locator('#discard-trump')).to_be_enabled()
            mouse_drag(a, 60, 0)
            assert_count(a, before - 2)
            report['gestures'].append('mouse: right60px discards without a drop target')

            a.set_viewport_size({'width': 390, 'height': 844})
            expect(a.locator('.trump-card')).to_have_count(3)
            a.locator('.trump-card').first.click()
            expect(a.locator('#use-trump')).to_be_enabled()
            touch_drag(a, 0, -44)
            assert_count(a, before - 3)
            report['gestures'].append('touch: up44px plays; browser page does not scroll')
            a.locator('.trump-card').first.click()
            expect(a.locator('#discard-trump')).to_be_enabled()
            touch_drag(a, 64, 0)
            assert_count(a, before - 4)
            report['gestures'].append('touch: right64px discards; no accidental hand scrolling')
            assert a.evaluate('scrollY') == 0

            a.locator('.trump-card').first.click()
            expect(a.locator('#discard-trump')).to_be_enabled()
            a.evaluate('window.holdNextPatch = true')
            old_actions = a.evaluate('window.uiWire.actions')
            started = time.monotonic()
            a.locator('#discard-trump').click()
            expect(a.locator('#turn-hint')).to_contain_text('正在确认弃牌')
            expect(a.locator('#discard-trump')).to_have_attribute('aria-busy', 'true')
            assert_count(a, before - 5)
            a.locator('.trump-card').first.click()
            expect(a.locator('#discard-trump')).to_be_enabled()
            assert a.evaluate('window.uiWire.actions') == old_actions + 1, 'Recovery must not replay the move'
            assert a.evaluate('window.uiWire.syncs') >= 1
            report['recovery']['withheld_update_seconds'] = round(time.monotonic() - started, 3)
            report['recovery']['move_count'] = 'one accepted discard; no replay'

            a.locator('#journal-open').click()
            expect(a.locator('#journal-dialog')).to_be_visible()
            expect(a.locator('#events li')).not_to_have_count(0)
            a.locator('#journal-close').click()
            a.locator('#surrender').click()
            a.locator('#confirm-ok').click()
            expect(a.locator('#rematch')).to_be_visible()
            expect(a.locator('#turn-hint')).to_contain_text('本场结束')
            expect(a.locator('#stay')).to_be_disabled()
            for page in [a, b]:
                page.locator('#rematch').click()
            expect(a.locator('#rematch')).to_be_hidden()
            expect(a.locator('#game')).to_be_visible()
            assert not errors, errors
            (OUT / 'web-ui-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print('PASS: seven viewports, always-visible hands/actions, mouse/touch short gestures, pagination, auth recovery, delayed update recovery without replay, journal and rematch; zero browser errors')
        finally:
            for index, page in enumerate(browser.contexts):
                for tab in page.pages:
                    if not tab.is_closed():
                        tab.screenshot(path=str(OUT / f'web-ui-final-{index}.png'), animations='disabled')
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-path', default='/re7')
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    # Retain diagnostics. On Windows another process can briefly retain a log
    # handle after server exit, making deletion with a temporary folder fail.
    log_path = OUT / 'web-ui-server.log'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, NOIR_ORIGIN='', NOIR_BASE_PATH=args.base_path)
    with log_path.open('w', encoding='utf-8') as log:
        script = f"import importlib,uvicorn; module=importlib.import_module('web.app'); module.AUTH_TIMEOUT=.5; uvicorn.run(module.app,host='127.0.0.1',port={port})"
        process = subprocess.Popen([sys.executable, '-c', script], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            url = f'http://127.0.0.1:{port}' + args.base_path.rstrip('/')
            for _ in range(100):
                try:
                    urllib.request.urlopen(url + '/healthz', timeout=.5).close()
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError(log_path.read_text(encoding='utf-8'))
                    time.sleep(.1)
            else:
                raise RuntimeError('The UI acceptance server did not start')
            check_usability(url)
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
