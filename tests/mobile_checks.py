"""Timed mobile layouts exercised through real rooms and accepted moves."""
import json
from playwright.sync_api import expect


def check_timed_mobile(browser, url, config, seat_key, output):
    pages = [browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True)
             for _ in range(2)]
    errors = []
    config = json.loads(json.dumps(config))
    config['game_settings'].update(target_score=100, hit_draw_trump_probability=0)
    a, b = pages
    try:
        for page in pages:
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
            page.goto(url)
        response = a.request.post(url + '/api/rooms', data={
            'name': '手机计时测试', 'config': config,
            'timer': {'enabled': True, 'mode': 'fischer', 'initial_minutes': 30,
                      'increment_seconds': 3, 'preparation_seconds': None}})
        assert response.ok, response.text()
        host = response.json()
        response = b.request.post(url + f"/api/rooms/{host['room']}/join", data={'name': '计时对手'})
        assert response.ok, response.text()
        for page, seat in zip(pages, [host, response.json()]):
            page.evaluate('([key, seat]) => sessionStorage.setItem(key, JSON.stringify(seat))', [seat_key, seat])
            page.goto(url + '/?room=' + host['room'])
            expect(page.locator('#ready')).to_be_enabled()
        for page in pages:
            expect(page.locator('#lobby-players .lobby-seat').nth(1)).to_contain_text('已入座')
        a.locator('#ready').click()
        expect(b.locator('#lobby-players .lobby-seat').first).to_contain_text('已准备')
        b.locator('#ready').click()
        expect(a.locator('#game')).to_have_attribute('data-timed', 'true')
        expect(a.locator('#stay')).to_be_enabled()
        sizes = [(390, 844), (360, 640), (320, 568), (844, 390), (667, 375), (568, 320)]
        report = []
        for dense in [False, True]:
            if dense:
                # Three field effects force pagination on narrow screens.
                for _ in range(3):
                    a.locator('.trump-card').first.click()
                    a.locator('.trump-card').first.press('ArrowUp')
                    expect(a.locator('#stay')).to_be_enabled()
                # Grow the real number hand to two rows without ending the round.
                a.locator('#hit').click()
                expect(b.locator('#stay')).to_be_enabled()
                b.locator('#stay').click()
                for count in range(4, 9):
                    expect(a.locator('#hit')).to_be_enabled()
                    a.locator('#hit').click()
                    expect(a.locator('#my-player .number-card')).to_have_count(count)
                    if count < 8:
                        expect(b.locator('#stay')).to_be_enabled()
                        b.locator('#stay').click()
            for width, height in sizes:
                a.set_viewport_size({'width': width, 'height': height})
                a.wait_for_timeout(80)
                geometry = a.evaluate('''() => {
                  const box = node => {const r=node.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
                  return {
                    width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,
                    table:box(document.getElementById('play-drop')),
                    clocks:[...document.querySelectorAll('.player-clock .clock')].map(box),
                    hud:[...document.querySelectorAll('#target,#deck-label,#stake-label,.player-clock')].map(box),
                    cards:[...document.querySelectorAll('.number-card')].map(box),
                    field:[...document.querySelectorAll('.effect')].map(box),
                    header:[...document.querySelectorAll('.masthead button,.room-links button')].filter(n=>n.getBoundingClientRect().width).map(n=>{const r=n.getBoundingClientRect();const target=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return n===target||n.contains(target);})
                  };
                }''')
                assert geometry['scrollWidth'] == width and geometry['scrollHeight'] == height, geometry
                assert all(geometry['header']), geometry
                for box in geometry['hud'] + geometry['clocks']:
                    assert box['width'] > 0 and box['height'] > 0 and box['x'] >= 0 and box['right'] <= width + 1, geometry
                    assert box['y'] >= 0 and box['bottom'] <= geometry['table']['y'], geometry
                for box in geometry['cards'] + geometry['field']:
                    assert box['width'] >= 14 and box['height'] >= 20, geometry
                    table = geometry['table']
                    assert box['x'] >= table['x'] and box['right'] <= table['right'] + 1, geometry
                    assert box['y'] >= table['y'] and box['bottom'] <= table['bottom'] + 1, geometry
                if dense and width <= 360:
                    expect(a.locator('#field-next')).to_be_enabled()
                    a.locator('#field-next').click()
                    expect(a.locator('#field-prev')).to_be_enabled()
                    a.locator('#field-prev').click()
                if not dense and width == 390:
                    assert geometry['table']['height'] / height > .60, geometry
                    toggle = a.locator('#fullscreen-toggle')
                    toggle.click()
                    expect(toggle).to_have_attribute('aria-pressed', 'true')
                    assert a.evaluate('document.fullscreenElement === document.documentElement')
                    toggle.click()
                    expect(toggle).to_have_attribute('aria-pressed', 'false')
                    expect(a.locator('#stay')).to_be_enabled()
                a.screenshot(path=str(output / f'mobile-timed-{width}x{height}-{"dense" if dense else "normal"}.png'), animations='disabled')
                report.append({'width': width, 'height': height, 'dense': dense, 'tableHeight': geometry['table']['height']})
        # Leaving the table must not strand a touch user in fullscreen at home.
        a.locator('#fullscreen-toggle').click()
        expect(a.locator('#fullscreen-toggle')).to_have_attribute('aria-pressed', 'true')
        a.locator('#leave-room').click()
        a.locator('#confirm-ok').click()
        expect(a.locator('#home')).to_be_visible()
        expect(a.locator('#fullscreen-toggle')).to_be_visible()
        a.locator('#fullscreen-toggle').click()
        expect(a.locator('#fullscreen-toggle')).to_have_attribute('aria-pressed', 'false')
        assert not errors, errors
        return report
    finally:
        for page in pages:
            page.close()
