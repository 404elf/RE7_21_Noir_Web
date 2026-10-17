"""Exercise the shipped gesture module with throws, discards and cancellations."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope='module')
def gesture_page():
    source = Path(__file__).resolve().parents[1] / 'web/static/gestures.js'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('http://gestures.test/', lambda route: route.fulfill(body='<html></html>'))
        page.route('http://gestures.test/gestures.js', lambda route: route.fulfill(
            content_type='text/javascript', body=source.read_text(encoding='utf-8')))
        page.goto('http://gestures.test/')
        page.evaluate("async () => { window.gestureAction = (await import('./gestures.js')).gestureAction; }")
        yield page
        browser.close()


@pytest.mark.parametrize('dx,dy,touch,table,expected', [
    (0, -40, False, False, 'TRUMP'),
    (0, -44, True, False, 'TRUMP'),
    (100, -40, False, False, 'TRUMP'),
    (-100, -40, False, False, 'TRUMP'),
    (92, -44, True, False, 'TRUMP'),
    (-120, -36, True, False, 'TRUMP'),
    (600, -60, False, True, 'TRUMP'),
    (-500, -50, True, True, 'TRUMP'),
    (120, -20, True, True, 'TRUMP'),
    (60, 0, False, False, 'DISCARD'),
    (64, 12, True, False, 'DISCARD'),
    (100, -10, True, False, 'DISCARD'),
    (8, -6, False, False, None),
    (8, -6, True, True, None),
    (0, 0, False, False, None),
    (-60, 30, False, False, None),
    (0, 60, True, False, None),
])
def test_throw_intent(gesture_page, dx, dy, touch, table, expected):
    actual = gesture_page.evaluate('args => window.gestureAction(...args)', [dx, dy, touch, table])
    assert actual == expected
