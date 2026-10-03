"""Two independent Chromium browsers complete a match through the actual Web UI.

python tests/web_e2e.py           # isolated local server, fast test configuration
python tests/web_e2e.py --url ... # an already running server with its own rules
"""
import argparse
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect
from web_custom_e2e import check_customization

ROOT = Path(__file__).resolve().parents[1]


def run(url, fast=False):
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    failures = []
    frames = []
    with sync_playwright() as p:
        browsers = [p.chromium.launch(), p.chromium.launch()]
        try:
            pages = [b.new_page(viewport={"width": 1440, "height": 1050}) for b in browsers]
            for page in pages:
                page.add_init_script("window.WebSocket = new Proxy(window.WebSocket, {construct(Target, args) { const ws = Reflect.construct(Target, args); window.noirTestSocket = ws; return ws; }});")
                page.on("websocket", lambda ws: ws.on("framereceived", lambda value: frames.append(json.loads(value))))
                page.on("pageerror", lambda error: failures.append(str(error)))
                page.on("console", lambda message: failures.append(message.text) if message.type == "error" else None)
                page.goto(url)
            a, b = pages
            a.screenshot(path=str(output / "web-home.png"), full_page=True, animations="disabled")
            a.locator("#nickname").fill("房主 Alice")
            a.locator("#create-room").click()
            a.locator("#ready").wait_for(state="visible")
            expect(a.locator("#ready")).to_be_enabled()
            code = a.locator("#room-label").inner_text()
            assert a.url == f"{url}/?room={code}"
            a.evaluate("navigator.clipboard.writeText = async value => { window.noirInvite = value; }")
            a.locator("#copy-invite").click()
            assert a.evaluate("window.noirInvite") == f"{url}/?room={code}"
            b.goto(f"{url}/?room={code}")
            assert b.locator("#room-code").input_value() == code
            b.locator("#nickname").fill("对手 Bob")
            b.locator("#join-room").click()
            expect(b.locator("#ready")).to_be_visible()
            expect(b.locator("#ready")).to_be_enabled()
            expect(a.locator("#lobby-players")).to_contain_text("对手 Bob")
            a.locator("#ready").click()
            expect(b.locator("#lobby-players")).to_contain_text("已准备")
            b.locator("#ready").click()
            for page in pages:
                page.locator("#game").wait_for(state="visible")
                assert page.locator("#opponent .hidden-card").count() == 1
            if a.locator(".trump-card").count():
                a.locator(".trump-card").first.click()
            a.screenshot(path=str(output / "web-table.png"), full_page=True, animations="disabled")
            if fast:
                # An unchanged, unlimited-time hand produces no state traffic.
                checkpoint = len(frames)
                a.wait_for_timeout(1100)
                assert not [f for f in frames[checkpoint:] if f["type"] in ("state", "patch", "clock")]
                a.locator(".trump-card").first.click()
                a.locator("#use-trump").click()
                expect(a.locator("#my-effects")).to_contain_text("加注")
                assert "轮到你" in a.locator("#turn-hint").inner_text()
                remaining = a.locator(".trump-card").count()
                a.locator(".trump-card").first.click()
                expect(a.locator("#discard-trump")).to_be_enabled()
                bounds = a.locator('.trump-card').first.bounding_box()
                x, y = bounds['x'] + bounds['width']/2, bounds['y'] + bounds['height']/2
                a.mouse.move(x, y); a.mouse.down()
                a.mouse.move(x+60, y, steps=5); a.mouse.up()
                expect(a.locator(".trump-card")).to_have_count(remaining - 1)
                # A lost baseline must request one full sync and recover the same hand.
                checkpoints = sum(f["type"] == "state" for f in frames)
                total_before_sync = a.locator("#my-player .total strong").inner_text()
                a.evaluate("noirTestSocket.dispatchEvent(new MessageEvent('message', {data: JSON.stringify({type:'patch', base_revision:-1, revision:100000})}))")
                for _ in range(30):
                    if sum(f["type"] == "state" for f in frames) > checkpoints:
                        break
                    a.wait_for_timeout(50)
                assert sum(f["type"] == "state" for f in frames) == checkpoints + 1
                assert a.locator("#my-player .total strong").inner_text() == total_before_sync
            # Reload preserves seat, hidden-card view, and the same match.
            before = b.locator("#my-player .total strong").inner_text()
            b.reload()
            b.locator("#game").wait_for(state="visible")
            expect(b.locator("#connection")).to_contain_text("已连接")
            assert b.locator("#my-player .total strong").inner_text() == before
            deadline = time.monotonic() + 240
            moves = 0
            while time.monotonic() < deadline:
                if a.locator("#rematch").is_visible() and b.locator("#rematch").is_visible():
                    break
                acted = False
                for page in pages:
                    if not page.locator("#stay").is_enabled():
                        continue
                    total = int(page.locator("#my-player .total strong").inner_text())
                    target = int(page.locator("#target").inner_text())
                    button = "#hit" if total < target - 4 and page.locator("#hit").is_enabled() else "#stay"
                    page.locator(button).click()
                    moves += 1
                    acted = True
                    page.wait_for_timeout(570)
                    break
                if not acted:
                    a.wait_for_timeout(100)
            else:
                raise AssertionError("The browsers did not finish a health-based match in 240 seconds")
            assert "本场" in a.locator("#result-title").inner_text()
            assert a.locator("#opponent .hidden-card").count() == 0
            a.screenshot(path=str(output / "web-result.png"), full_page=True, animations="disabled")
            a.locator("#rematch").click()
            expect(a.locator("#rematch")).to_contain_text("等待")
            b.wait_for_timeout(300)
            b.locator("#rematch").click()
            expect(a.locator("#rematch")).to_be_hidden()
            expect(a.locator("#round-label")).to_contain_text("01")
            b.set_viewport_size({"width": 390, "height": 844})
            b.screenshot(path=str(output / "web-mobile.png"), full_page=True, animations="disabled")
            assert b.evaluate("document.documentElement.scrollWidth <= innerWidth")
            b.locator("#rules-open").click()
            b.locator("#catalog-search").fill("护盾")
            assert b.locator("#catalog article").count() > 0
            b.locator("#rules-close").click()
            b.emulate_media(reduced_motion="reduce")
            assert b.evaluate("getComputedStyle(document.querySelector('.target-seal'), '::before').animationName") == "none"
            b.locator("#sound-toggle").click()
            expect(b.locator("#sound-toggle")).to_have_attribute("aria-pressed", "true")
            assert any(f["type"] == "patch" for f in frames)
            assert all("rules" not in f["changes"] for f in frames if f["type"] == "patch")
            assert not failures, failures
            b.locator("#leave-room").click()
            if b.locator("#confirm-dialog").is_visible():
                b.locator("#confirm-ok").click()
            expect(b.locator("#home")).to_be_visible()
            assert b.url == url + "/"
            b.reload()
            expect(b.locator("#home")).to_be_visible()
            print(f"PASS: two Chromium instances, create/join, private cards, {'trumps/discard, ' if fast else ''}refresh reconnect, {moves} moves, health-based gameover, rematch, mobile and catalog; zero browser errors")
            print(f"Screenshots: {output}")
            print(f"Wire messages: { {kind: sum(f['type'] == kind for f in frames) for kind in ('state', 'patch', 'clock', 'pong')} }; idle traffic and patch-only actions checked")
            sizes = {kind: [len(json.dumps(f, ensure_ascii=False, separators=(',', ':')).encode()) for f in frames if f['type'] == kind] for kind in ('state', 'patch', 'clock')}
            report = {kind: dict(count=len(values), total_bytes=sum(values), min_bytes=min(values) if values else 0, max_bytes=max(values) if values else 0) for kind, values in sizes.items()}
            (output / "web-network.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Uncompressed message bytes: {report}")
        finally:
            for browser in browsers:
                browser.close()


def check_clock(url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        frames = []
        try:
            a, b = browser.new_page(), browser.new_page()
            a.on('websocket', lambda ws: ws.on('framereceived', lambda value: frames.append(json.loads(value))))
            a.goto(url); a.locator('#create-room').click(); expect(a.locator('#ready')).to_be_visible(); expect(a.locator('#ready')).to_be_enabled()
            code = a.locator('#room-label').inner_text()
            b.goto(f'{url}/?room={code}'); b.locator('#join-room').click(); expect(b.locator('#ready')).to_be_visible(); expect(b.locator('#ready')).to_be_enabled()
            a.locator('#ready').click(); expect(b.locator('#lobby-players')).to_contain_text('已准备')
            b.locator('#ready').click(); a.locator('#game').wait_for(state='visible')
            first = int(re.search(r'\d+', a.locator('#clock-1').inner_text()).group())
            count = len(frames)
            a.wait_for_timeout(1250)
            second = int(re.search(r'\d+', a.locator('#clock-1').inner_text()).group())
            assert second < first
            assert not [f for f in frames[count:] if f['type'] in ('patch', 'clock', 'state')]
            for _ in range(100):
                if any(f['type'] == 'clock' for f in frames):
                    break
                a.wait_for_timeout(50)
            assert any(f['type'] == 'clock' for f in frames)
            a.locator('#hit').click()
            expect(b.locator('#stay')).to_be_enabled()
            b.goto('about:blank')
            expect(a.locator('#notice')).to_contain_text('暂停')
            frozen = a.locator('#clock-2').inner_text(); a.wait_for_timeout(1250)
            assert a.locator('#clock-2').inner_text() == frozen
            b.goto(url); expect(b.locator('#game')).to_be_visible()
            expect(b.locator('#stay')).to_be_enabled()
            a.locator('#match-menu summary').click()
            a.locator('#surrender').click(); expect(a.locator('#confirm-dialog')).to_be_visible()
            a.locator('#confirm-cancel').click(); expect(a.locator('#confirm-dialog')).to_be_hidden()
            assert not a.locator('#rematch').is_visible()
            print('PASS: browser-local countdown without state traffic, 5-second calibration, paused clocks, reconnect and cancel surrender')
        finally:
            browser.close()


def check_heartbeat(url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.add_init_script("window.noirConnections = 0; window.noirCloseCodes = []; window.WebSocket = new Proxy(window.WebSocket, {construct(Target, args) { const ws = Reflect.construct(Target, args); window.noirConnections++; ws.addEventListener('close', e => noirCloseCodes.push(e.code)); return ws; }});")
            page.goto(url)
            page.locator('#create-room').click()
            expect(page.locator('#ready')).to_be_visible()
            expect(page.locator('#ready')).to_be_enabled()
            room = page.locator('#room-label').inner_text()
            saved = page.evaluate("Object.entries(sessionStorage).find(([key]) => key.endsWith('noir-seat'))")
            assert saved
            # This server uses a 1.5-second heartbeat deadline; the browser's
            # first ping is at 10 seconds, causing an actual server timeout.
            page.wait_for_function("() => noirCloseCodes.includes(1012) && noirConnections >= 2")
            expect(page.locator('#ready')).to_be_enabled()
            assert page.evaluate("sessionStorage.getItem(" + json.dumps(saved[0]) + ")") == saved[1]
            assert page.locator('#room-label').inner_text() == room
            print('PASS: actual heartbeat timeout closes with 1012, retains browser seat and automatically reconnects')
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    parser.add_argument("--base-path", default="", help="exercise a configured application path, e.g. /re7")
    args = parser.parse_args()
    if args.url:
        run(args.url.rstrip("/"))
        check_customization(args.url.rstrip("/"))
        return
    with tempfile.TemporaryDirectory(prefix="noir-web-e2e-") as temp:
        directory = Path(temp)
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
        config["game_settings"].update(max_hp=2, initial_trumps_count=1, number_card_draw_probability=0)
        config["trump_weights"] = {k: 0 for k, v in config["trump_weights"].items() if isinstance(v, (int, float))}
        config["trump_weights"]["Add 1"] = 1
        (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (directory / "timer.json").write_text('{"enabled":false,"settlement_seconds":0.4}', encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = dict(os.environ, NOIR_CONFIG=str(directory / "config.json"), NOIR_TIMER=str(directory / "timer.json"), NOIR_ORIGIN="", NOIR_BASE_PATH=args.base_path)
        with (directory / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", str(port), "--ws-max-size", "2048"], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                url = f"http://127.0.0.1:{port}" + args.base_path.rstrip("/")
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError((directory / "server.log").read_text(encoding="utf-8"))
                    try:
                        urllib.request.urlopen(url + "/healthz", timeout=.5).close()
                        break
                    except OSError:
                        time.sleep(.1)
                run(url, fast=True)
                check_customization(url)
            finally:
                process.terminate()
                process.wait(timeout=10)
        (directory / 'timer.json').write_text('{"enabled":true,"mode":"turn","turn_seconds":30,"preparation_seconds":12,"settlement_seconds":0.4}', encoding='utf-8')
        with (directory / 'clock-server.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'web.app:app', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(url + '/healthz', timeout=.5).close(); break
                    except OSError:
                        time.sleep(.1)
                check_clock(url)
            finally:
                process.terminate(); process.wait(timeout=10)
        with (directory / 'heartbeat-server.log').open('w', encoding='utf-8') as log:
            command = f"import uvicorn,web.app; web.app.HEARTBEAT_TIMEOUT=1.5; uvicorn.run(web.app.app,host='127.0.0.1',port={port},log_level='warning')"
            process = subprocess.Popen([sys.executable, '-c', command], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(url + '/healthz', timeout=.5).close(); break
                    except OSError:
                        time.sleep(.1)
                check_heartbeat(url)
            finally:
                process.terminate(); process.wait(timeout=10)


if __name__ == "__main__":
    main()
