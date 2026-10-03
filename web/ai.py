"""Run the original fair-information strategies outside the game event loop."""
from bot import DIFFICULTIES, STYLES
from rules import rules_active
import signal


def checked(value):
    if (not isinstance(value, dict) or set(value) != {'difficulty', 'style'}
            or value['difficulty'] not in DIFFICULTIES or value['style'] not in STYLES):
        raise ValueError('invalid_ai')
    return dict(value)


def choose(strategy, observation, extra, rules):
    # Each process handles one decision at a time. Its temporary engine globals
    # and hypothetical RNG cannot race with live rooms or consume their RNG.
    def expired(signum, frame):
        raise TimeoutError('AI planning budget exceeded')

    # Linux deployment: protect short clocks and CPU even with a custom 100-card
    # hand. Standard positions retain the original algorithm unchanged.
    bounded = hasattr(signal, 'setitimer')
    old = signal.signal(signal.SIGALRM, expired) if bounded else None
    if bounded:
        signal.setitimer(signal.ITIMER_REAL, 2)
    try:
        with rules_active(rules):
            return strategy.choose(observation, extra), strategy
    except TimeoutError:
        return 'STAY', strategy
    finally:
        if bounded:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)
