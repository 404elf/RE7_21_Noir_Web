"""The shipped screen controls must follow browser state, not optimistic labels."""
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

STATIC = Path(__file__).resolve().parents[1] / 'web/static'
ORIGIN = 'http://screen.test'


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={'width': 390, 'height': 844})
    page.route(ORIGIN + '/re7/screen.js', lambda route: route.fulfill(
        content_type='text/javascript', body=(STATIC / 'screen.js').read_text(encoding='utf-8')))
    markup = (STATIC / 'index.html').read_text(encoding='utf-8')
    # Use the real controls and dialog structure, isolated from the game server.
    markup = markup.replace('<script type="module" src="./app.js"></script>', '')
    page.route(ORIGIN + '/re7/', lambda route: route.fulfill(content_type='text/html', body=markup))
    page.route('**/*.css', lambda route: route.fulfill(content_type='text/css', body=''))
    page.goto(ORIGIN + '/re7/')
    page.evaluate('''async () => {
      document.getElementById('room').hidden = false;
      document.getElementById('game').hidden = false;
      (await import('./screen.js')).initScreenControls();
    }''')
    yield page
    page.close()


def test_native_fullscreen_enter_exit_and_external_exit(page):
    toggle = page.locator('#fullscreen-toggle')
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    assert page.evaluate('document.fullscreenElement === document.documentElement')
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    page.evaluate('document.exitFullscreen()')
    expect(toggle).to_have_text('全屏')
    expect(toggle).to_have_attribute('aria-pressed', 'false')


def test_unsupported_browser_explains_home_screen_without_false_state(page):
    page.evaluate('''() => {
      Object.defineProperty(document, 'fullscreenEnabled', {value:false});
      Object.defineProperty(document, 'webkitFullscreenEnabled', {value:false});
    }''')
    page.locator('#fullscreen-toggle').click()
    expect(page.locator('#fullscreen-help')).to_be_visible()
    expect(page.locator('#fullscreen-help-copy')).to_contain_text('未开放网页全屏')
    expect(page.locator('#fullscreen-toggle')).to_have_attribute('aria-pressed', 'false')
    page.keyboard.press('Escape')
    expect(page.locator('#fullscreen-help')).to_be_hidden()


def test_fullscreen_rejection_can_retry(page):
    page.evaluate('''() => {
      window.requestNative = document.documentElement.requestFullscreen;
      document.documentElement.requestFullscreen = () => Promise.reject(new TypeError('denied'));
    }''')
    toggle = page.locator('#fullscreen-toggle')
    toggle.click()
    expect(page.locator('#fullscreen-help-copy')).to_contain_text('未能切换全屏')
    expect(toggle).to_be_enabled()
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    page.locator('#fullscreen-help-close').click()
    page.evaluate('() => { document.documentElement.requestFullscreen = window.requestNative; }')
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'true')


def test_mobile_menu_uses_same_buttons_and_restores_desktop(page):
    page.evaluate("window.originalDraw = document.getElementById('offer-draw')")
    page.locator('#table-menu-open').click()
    expect(page.locator('#table-menu-dialog')).to_be_visible()
    page.locator('#offer-draw').click()
    expect(page.locator('#table-menu-dialog')).to_be_hidden()
    page.locator('#table-menu-open').click()
    page.set_viewport_size({'width': 1440, 'height': 900})
    expect(page.locator('#table-menu-dialog')).to_be_hidden()
    assert page.evaluate("document.querySelector('.game-console #offer-draw') === window.originalDraw")
    page.set_viewport_size({'width': 844, 'height': 390})
    expect(page.locator('#table-menu-dialog #offer-draw')).to_have_count(1)


def test_installed_mode_has_no_redundant_fullscreen_entry(page):
    page.evaluate("Object.defineProperty(navigator, 'standalone', {value:true}); document.dispatchEvent(new Event('fullscreenchange'))")
    expect(page.locator('#fullscreen-toggle')).to_be_hidden()
