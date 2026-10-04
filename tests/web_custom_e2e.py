"""Real UI acceptance for independent house rules, desktop JSON and consent."""
import copy
import json
from pathlib import Path
import tempfile
import time

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def check_customization(url):
    output = ROOT / '.artifacts'
    output.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as p, tempfile.TemporaryDirectory(prefix='noir-settings-') as temp:
        browser = p.chromium.launch()
        try:
            pages = [browser.new_page(viewport={'width': 1440, 'height': 1050}) for _ in range(4)]
            for page in pages:
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
                page.goto(url)
            a, b, c, d = pages
            a.locator('#customize-room').click()
            expect(a.locator('#settings-dialog')).to_be_visible()
            assert a.locator('#settings-preset option').count() == 10
            locked_hp = a.locator('#setting-max_hp').input_value()
            a.locator('[aria-label="随机时锁定max_hp"]').click()
            a.locator('#settings-roll').click()
            expect(a.locator('#settings-status')).to_contain_text('保留 1 个锁定项')
            expect(a.locator('#setting-max_hp')).to_have_value(locked_hp)
            rules_before = a.locator('#settings-game input').evaluate_all('nodes => nodes.map(n => n.value)')
            a.locator('#settings-roll-weights').click()
            expect(a.locator('#settings-status')).to_contain_text('随机权重')
            assert a.locator('#settings-game input').evaluate_all('nodes => nodes.map(n => n.value)') == rules_before
            weights = a.locator('#settings-weights input[type=number]').evaluate_all('nodes => nodes.map(n => Number(n.value))')
            assert len(weights) == 42 and set(weights) <= {0,2,4,6,8,10}
            a.locator('#settings-close').press('Control+A')
            assert a.evaluate('getSelection().toString()') == ''
            a.locator('[data-settings-tab="timer"]').click()
            expect(a.locator('#timer-presets button')).to_have_count(12)
            a.locator('[data-clock="5+3"]').click()
            a.locator('[data-clock="custom"]').click()
            expect(a.locator('#timer-preset-view')).to_be_hidden()
            expect(a.locator('#timer-initial_minutes')).to_have_value('5')
            a.locator('#timer-presets-back').click()
            expect(a.locator('#timer-custom')).to_be_hidden()
            a.locator('[data-settings-tab="game"]').click()
            # Import an unmodified original preset with _name_* / _c_* metadata.
            a.locator('#settings-file').set_input_files(ROOT / 'presets' / '娱乐版.json')
            expect(a.locator('#settings-status')).to_contain_text('已载入 娱乐版.json')
            a.locator('#settings-preset').select_option(label='烛火局')
            expect(a.locator('#setting-max_hp')).to_have_value('5')
            a.locator('#setting-max_hp').fill('2')
            a.locator('#setting-target_score').fill('27')
            a.locator('#setting-deck_range_end').fill('17')
            a.locator('#setting-initial_trumps_count').fill('2')
            a.locator('#setting-round_reward_trumps_count').fill('0')
            a.locator('#setting-number_card_draw_probability').fill('0')
            a.locator('[data-settings-tab="weights"]').click()
            a.locator('#settings-weights input[type=checkbox]').evaluate_all('nodes => nodes.forEach(n => n.checked = false)')
            a.locator('#settings-weight-search').fill('加注 1')
            a.locator('[id="weight-Add 1"]').fill('1')
            a.locator('#settings-weight-search').fill('ADD2+')
            a.locator('[id="weight-Return+"]').fill('2')
            with a.expect_download() as download:
                a.locator('#settings-export-config').click()
            config_file = Path(temp) / 'config.json'
            download.value.save_as(config_file)
            exported = json.loads(config_file.read_text(encoding='utf-8'))
            assert exported['game_settings']['target_score'] == 27
            assert exported['trump_weights']['Return+'] == 2 and 'ADD2+' not in exported['trump_weights']
            a.locator('[id="weight-Return+"]').fill('0')
            timer = dict(enabled=True, mode='fischer', initial_minutes=None, increment_seconds=3,
                         turn_seconds=None, round_seconds=120, preparation_seconds=None, settlement_seconds=.1)
            timer_file = Path(temp) / 'timer.json'
            timer_file.write_text(json.dumps(timer), encoding='utf-8-sig')
            a.locator('#settings-file').set_input_files(timer_file)
            expect(a.locator('#settings-status')).to_contain_text('已载入 timer.json')
            expect(a.locator('#timer-initial_minutes-unlimited')).to_be_checked()
            with a.expect_download() as download:
                a.locator('#settings-export-timer').click()
            exported_timer = Path(temp) / 'exported-timer.json'
            download.value.save_as(exported_timer)
            assert json.loads(exported_timer.read_text(encoding='utf-8')) == timer
            a.locator('[data-settings-tab="timer"]').click()
            a.locator('[data-clock="custom"]').click()
            a.screenshot(path=str(output / 'web-custom-settings.png'), animations='disabled')
            a.locator('#settings-apply').click()
            expect(a.locator('#settings-dialog')).to_be_hidden()
            expect(a.locator('#new-settings-summary')).to_contain_text('目标 27')
            a.locator('#create-room').click()
            expect(a.locator('#ready')).to_be_enabled()
            room_a = a.locator('#room-label').inner_text()

            # Another simultaneous room has another target, pool, HP and clock.
            other = copy.deepcopy(exported)
            other['game_settings'].update(max_hp=2, target_score=21, deck_range_end=11, initial_trumps_count=1)
            other['trump_weights'] = dict.fromkeys(other['trump_weights'], 0)
            other['trump_weights']['Shield'] = 1
            other_file = Path(temp) / 'other-room.json'
            other_file.write_text(json.dumps({'config': other, 'timer': {**timer, 'enabled': False}}), encoding='utf-8')
            c.locator('#customize-room').click()
            c.locator('#settings-file').set_input_files(other_file)
            expect(c.locator('#settings-status')).to_contain_text('已载入 other-room.json')
            c.locator('#settings-apply').click()
            expect(c.locator('#settings-dialog')).to_be_hidden()
            c.locator('#create-room').click()
            expect(c.locator('#ready')).to_be_enabled()
            room_b = c.locator('#room-label').inner_text()
            assert room_a != room_b
            for page, code in [(b, room_a), (d, room_b)]:
                page.goto(f'{url}/?room={code}')
                page.locator('#join-room').click()
                expect(page.locator('#ready')).to_be_enabled()
                expect(page.locator('#edit-room-settings')).to_be_hidden()
            expect(a.locator('#lobby-players')).to_contain_text('已入座')
            expect(c.locator('#lobby-players')).to_contain_text('已入座')
            b.locator('#room-settings').click()
            expect(b.locator('#setting-target_score')).to_have_value('27')
            expect(b.locator('#setting-target_score')).to_be_disabled()
            expect(b.locator('#settings-apply')).to_be_hidden()
            a.locator('#ready').click()
            expect(b.locator('#lobby-players')).to_contain_text('已准备')
            a.locator('#edit-room-settings').click()
            a.locator('#setting-max_hp').fill('1')
            a.locator('#settings-apply').click()
            expect(a.locator('#settings-dialog')).to_be_hidden()
            expect(b.locator('#setting-max_hp')).to_have_value('1')
            expect(b.locator('#settings-status')).to_contain_text('房主已更新规则')
            b.locator('#settings-close').click()
            expect(b.locator('#lobby-players')).not_to_contain_text('已准备')
            expect(c.locator('#lobby-settings')).to_contain_text('目标 21 · 生命 2')

            for host, guest in [(a, b), (c, d)]:
                host.locator('#ready').click()
                expect(guest.locator('#lobby-players')).to_contain_text('已准备')
                guest.locator('#ready').click()
                expect(host.locator('#game')).to_be_visible()
                expect(guest.locator('#game')).to_be_visible()
            expect(a.locator('#target')).to_have_text('27')
            expect(a.locator('#my-player .health')).to_have_text('♥ 1 / 1')
            expect(c.locator('#target')).to_have_text('21')
            expect(c.locator('#my-player .health')).to_have_text('♥ 2 / 2')
            expect(a.locator('.trump-card')).to_have_count(2)
            expect(c.locator('.trump-card')).to_have_count(1)
            a.locator('.trump-card').first.press('ArrowUp')
            expect(a.locator('#field-trumps')).to_contain_text('加注')
            c.locator('.trump-card').first.press('ArrowUp')
            expect(c.locator('#field-trumps')).to_contain_text('护盾')
            a.reload()
            expect(a.locator('#game')).to_be_visible()
            expect(a.locator('#target')).to_have_text('27')
            expect(a.locator('#my-player .health')).to_have_text('♥ 1 / 1')
            seat = a.evaluate("JSON.parse(Object.entries(sessionStorage).find(([key]) => key.endsWith('noir-seat'))[1])")
            response = a.request.post(f'{url}/api/rooms/{room_a}/settings', data={
                'config': other, 'timer': timer, 'token': seat['token'], 'revision': 0})
            assert response.status == 400 and '锁定' in response.json()['error']

            deadline, moves = time.monotonic() + 120, 0
            while time.monotonic() < deadline:
                if all(page.locator('#rematch').is_visible() for page in pages): break
                acted = False
                for page in pages:
                    if not page.locator('#stay').is_enabled(): continue
                    total = int(page.locator('#my-player .total strong').inner_text())
                    target = int(page.locator('#target').inner_text())
                    choice = '#hit' if total < target - 4 and page.locator('#hit').is_enabled() else '#stay'
                    page.locator(choice).click(); moves += 1; acted = True
                    page.wait_for_timeout(570)
                if not acted: a.wait_for_timeout(100)
            assert all(page.locator('#rematch').is_visible() for page in pages), 'Both custom matches must finish'
            a.locator('#rematch').click(); expect(a.locator('#rematch')).to_contain_text('等待')
            expect(a.locator('#rematch')).to_be_disabled()
            b.locator('#rematch').click(); expect(a.locator('#rematch')).to_be_hidden()
            expect(a.locator('.trump-card')).to_have_count(2)
            expect(a.locator('#target')).to_have_text('27')
            expect(a.locator('#my-player .health')).to_have_text('♥ 1 / 1')

            mobile = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            mobile.goto(url); mobile.locator('#customize-room').click()
            expect(mobile.locator('#settings-dialog')).to_be_visible()
            assert mobile.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
            assert mobile.locator('#settings-dialog').evaluate('(d) => { const r=d.getBoundingClientRect(); return r.left >= 0 && r.right <= document.documentElement.clientWidth; }')
            assert mobile.locator('#settings-dialog').evaluate('(d) => d.scrollWidth <= d.clientWidth')
            mobile.screenshot(path=str(output / 'web-custom-settings-mobile.png'), animations='disabled')
            mobile.locator('#settings-weight-search').fill('加注')
            assert mobile.locator('.weight-row:visible').count() > 0
            assert mobile.locator('#settings-dialog').evaluate('(d) => d.scrollWidth <= d.clientWidth')
            assert mobile.locator('#settings-dialog').evaluate('(d) => { const r=d.getBoundingClientRect(); return r.left >= 0 && r.right <= document.documentElement.clientWidth; }')
            mobile.screenshot(path=str(output / 'web-custom-weights-mobile.png'), animations='disabled')
            assert not errors, errors
            print(f'PASS: eight desktop presets, JSON import/export, null clocks, consent reset, live guest review, locked rules, two custom matches ({moves} moves), reconnect/rematch and mobile settings')
        finally:
            browser.close()
