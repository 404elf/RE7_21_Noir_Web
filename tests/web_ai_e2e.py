"""Use the real solo entry, original four difficulties, clock, refresh and rematch."""
import argparse
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect
from web_controls import fill_game

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.artifacts'


def run(url):
    errors, report = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for index, difficulty in enumerate(['easy','normal','hard','nightmare']):
                context = browser.new_context(viewport={'width':1440,'height':900} if index % 2 == 0 else {'width':390,'height':844})
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
                page.goto(url + '/')
                page.locator('#solo-open').click()
                expect(page.locator('#ai-difficulty option')).to_have_count(4)
                expect(page.locator('#ai-style option')).to_have_count(3)
                page.locator(f'#ai-difficulty-choices [data-value="{difficulty}"]').click()
                if difficulty == 'nightmare':
                    expect(page.locator('#ai-style')).to_be_disabled()
                else:
                    style = ['gambler','conservative','swing'][index]
                    page.locator(f'#ai-style-choices [data-value="{style}"]').click()
                page.locator('#solo-close').click()
                # Same per-room config editor as multiplayer; no server default mutation.
                page.locator('#customize-room').click()
                page.locator('#settings-game input').first.wait_for()
                for key, value in [('max_hp','2'),('initial_trumps_count','4'),
                                   ('round_reward_trumps_count','0'),('hit_draw_trump_probability','0'),
                                   ('number_card_draw_probability','0')]:
                    fill_game(page, key, value)
                page.locator('#settings-weights input').evaluate_all("nodes => nodes.forEach(n => n.value = n.id === 'weight-Add 1' ? '1' : '0')")
                page.locator('[data-settings-tab="timer"]').click()
                page.locator('[data-clock="custom"]').click()
                page.locator('#timer-enabled').check()
                page.locator('#timer-mode').select_option('turn')
                page.locator('#timer-turn_seconds').fill('30')
                page.locator('#timer-preparation_seconds').fill('30')
                page.locator('#timer-settlement_seconds').fill('0.1')
                page.locator('#settings-apply').click()
                expect(page.locator('#settings-dialog')).not_to_be_visible()
                page.locator('#solo-open').click()
                if index == 0:
                    page.screenshot(path=str(OUT / 'web-ai-setup.png'))
                page.locator('#solo-start').click()
                expect(page.locator('#lobby-players')).to_contain_text('AI')
                expect(page.locator('#copy-invite')).to_be_hidden()
                page.locator('#ready').click()
                expect(page.locator('#stay')).to_be_enabled()
                expect(page.locator('#opponent .hidden-card')).to_have_count(1)
                page.locator('.trump-card').first.click()
                page.locator('.trump-card').first.press('ArrowUp')
                expect(page.locator('#field-trumps')).to_contain_text('加注')
                page.locator('.trump-card').first.click()
                page.locator('.trump-card').first.press('ArrowRight')
                expect(page.locator('.trump-card')).to_have_count(2)
                hand = page.locator('#my-player .card-number').all_text_contents()
                page.reload()
                expect(page.locator('#game')).to_be_visible()
                expect(page.locator('#stay')).to_be_enabled()
                assert page.locator('#my-player .card-number').all_text_contents() == hand
                expect(page.locator('#field-trumps')).to_contain_text('加注')
                expect(page.locator('#clock-1')).to_contain_text(re.compile(r'\d'))
                page.locator('.trump-card').first.click()
                page.screenshot(path=str(OUT / f'web-ai-{difficulty}.png'))
                deadline, moves = time.monotonic() + 60, 0
                while not page.locator('#rematch').is_visible():
                    assert time.monotonic() < deadline, f'{difficulty} match stalled'
                    if page.locator('#stay').is_enabled():
                        total = int(page.locator('#my-player .total strong').inner_text())
                        page.locator('#hit' if total < 15 and page.locator('#hit').is_enabled() else '#stay').click()
                        moves += 1
                        # Wait for receipt / turn handoff, never spam pending actions.
                        page.wait_for_timeout(60)
                    else:
                        page.wait_for_timeout(50)
                page.locator('#rematch').click()
                expect(page.locator('#stay')).to_be_enabled()
                assert page.locator('#round-label').inner_text() == 'ROUND 01'
                report.append(dict(difficulty=difficulty, moves=moves, refresh=True, rematch=True))
                page.locator('#leave-room').click()
                page.locator('#confirm-ok').click()
                context.close()
            assert not errors, errors
            (OUT / 'web-ai-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print('PASS: original four AI difficulties, three style options, custom timed rules, play/discard, private cards, refresh, complete matches and rematches on PC/mobile; zero browser errors')
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url')
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.url:
        run(args.url.rstrip('/'))
        return
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port = listener.getsockname()[1]
    env = dict(os.environ, NOIR_ORIGIN='', NOIR_BASE_PATH='/re7')
    with (OUT / 'web-ai-server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable,'-m','uvicorn','web.app:app','--host','127.0.0.1','--port',str(port)], cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            url = f'http://127.0.0.1:{port}/re7'
            for _ in range(100):
                try:
                    urllib.request.urlopen(url+'/healthz',timeout=.5).close()
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError('Acceptance server exited; see web-ai-server.log')
                    time.sleep(.1)
            run(url)
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
