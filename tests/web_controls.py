"""Navigate the actual paged editor without relying on hidden controls."""


def fill_game(page, key, value):
    page.locator('[data-settings-tab="game"]').click()
    field = page.locator(f'#setting-{key}')
    while not field.is_visible() and page.locator('#game-setting-prev').is_enabled():
        page.locator('#game-setting-prev').click()
    while not field.is_visible() and page.locator('#game-setting-next').is_enabled():
        page.locator('#game-setting-next').click()
    field.fill(value)


def read_clock_seconds(page, pid):
    minutes, seconds = page.locator(f'#clock-{pid}').inner_text().split(':')
    return int(minutes) * 60 + int(seconds)
