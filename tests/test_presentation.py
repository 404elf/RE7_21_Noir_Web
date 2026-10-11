"""Exercise the shipped presentation module with real Chromium audio/animations."""
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

SOURCE = Path(__file__).resolve().parents[1] / 'web/static/presentation.js'
ORIGIN = 'http://presentation.test'


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    page.route(ORIGIN + '/re7/presentation.js', lambda route: route.fulfill(
        content_type='text/javascript', body=SOURCE.read_text(encoding='utf-8')))
    page.route(ORIGIN + '/re7/', lambda route: route.fulfill(
        content_type='text/html', body='''<!doctype html>
        <button id="sound-toggle"></button><canvas id="particles"></canvas>
        <span id="clock">0:12</span><div id="card">21</div>'''))
    page.add_init_script('''
      window.started = 0;
      const NativeAudio = AudioContext;
      window.AudioContext = class extends NativeAudio {
        constructor(...args) { super(...args); window.audioContext = this; }
        track(source) {
          const start = source.start.bind(source);
          source.start = (...args) => { window.started++; return start(...args); };
          return source;
        }
        createOscillator() { return this.track(super.createOscillator()); }
        createBufferSource() { return this.track(super.createBufferSource()); }
      };
      // Deterministically dispatch the same lifecycle event as a background tab.
      // AudioContext and animations remain real browser implementations.
      window.setHidden = hidden => {
        Object.defineProperty(document, 'hidden', {configurable: true, value: hidden});
        document.dispatchEvent(new Event('visibilitychange'));
      };
    ''')
    page.goto(ORIGIN + '/re7/')
    page.evaluate('''async () => {
      window.presentation = await import('./presentation.js');
      document.getElementById('sound-toggle').onclick = presentation.toggleSound;
      window.clock = (key, seconds, running = true) => presentation.clockFeedback(
        key, seconds, running, document.getElementById('clock'));
    }''')
    yield page
    page.close()


def enable_audio(page):
    page.locator('#sound-toggle').click()
    page.wait_for_function("() => window.audioContext?.state === 'running' && started > 0")


def test_background_return_resumes_opted_in_audio_without_another_click(page):
    enable_audio(page)
    page.evaluate('setHidden(true)')
    page.wait_for_function("() => audioContext.state === 'suspended'")
    before = page.evaluate('started')
    page.evaluate('setHidden(false)')
    page.wait_for_function("() => audioContext.state === 'running'", timeout=2000)
    assert page.evaluate('started') == before, 'Returning must not replay old cues'
    page.evaluate("presentation.submitted('HIT')")
    assert page.evaluate('started') > before


def test_saved_opt_in_stays_lazy_until_trusted_input(page):
    page.evaluate("localStorage.setItem('/re7/noir-sound', 'on')")
    page.reload()
    page.evaluate("async () => { window.presentation = await import('./presentation.js'); }")
    expect(page.locator('#sound-toggle')).to_have_attribute('aria-pressed', 'true')
    page.evaluate('setHidden(true); setHidden(false)')
    assert page.evaluate('window.audioContext === undefined')


def test_muted_background_return_stays_suspended(page):
    enable_audio(page)
    page.locator('#sound-toggle').click()
    page.evaluate('setHidden(true)')
    page.wait_for_function("() => audioContext.state === 'suspended'")
    before = page.evaluate('started')
    page.evaluate("setHidden(false); presentation.submitted('HIT')")
    assert page.evaluate('started') == before
    assert page.evaluate('audioContext.state') == 'suspended'


def test_clock_calibration_does_not_repeat_threshold_warnings(page):
    enable_audio(page)
    before = page.evaluate('started')
    page.evaluate("[12, 10, 11, 10, 4, 3, 4, 3].forEach(s => clock('turn-1', s))")
    assert page.evaluate('started') - before == 2, 'One warning per threshold per countdown'
    before = page.evaluate('started')
    page.evaluate("[12, 10, 3].forEach(s => clock('turn-2', s))")
    assert page.evaluate('started') - before == 2, 'A new turn must rearm both thresholds'


def test_background_cleanup_does_not_replay_elapsed_clock_warning(page):
    enable_audio(page)
    page.evaluate("clock('turn-1', 12); setHidden(true)")
    page.wait_for_function("() => audioContext.state === 'suspended'")
    # Background ticks may be throttled, but any that run must stay silent too.
    page.evaluate("clock('turn-1', 11)")
    page.evaluate('setHidden(false)')
    page.wait_for_function("() => audioContext.state === 'running'", timeout=2000)
    before = page.evaluate('started')
    page.evaluate("clock('turn-1', 5)")
    assert page.evaluate('started') == before
    page.evaluate("clock('turn-1', 3)")
    assert page.evaluate('started') == before + 1


def test_reduced_motion_cancels_existing_and_future_deals(page):
    page.evaluate("presentation.deal(document.getElementById('card'))")
    assert page.locator('#card').evaluate('(node) => node.getAnimations().length') == 1
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_function("() => !document.getElementById('card').getAnimations().length")
    page.evaluate("presentation.deal(document.getElementById('card'))")
    assert page.locator('#card').evaluate('(node) => node.getAnimations().length') == 0
