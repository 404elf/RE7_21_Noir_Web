"""Server-measured latency; no client wall clock or claimed ping is trusted."""
from collections import deque
import secrets

MAX_MOVE_CREDIT = .5
TURN_CREDIT_BUDGET = 1.
SAMPLE_TTL = 30.


class LagMeter:
    def __init__(self):
        self.samples = deque(maxlen=8)
        self.pending = None
        self.last_probe = float('-inf')

    def reset_connection(self):
        self.samples.clear()
        self.pending = None
        self.last_probe = float('-inf')

    def probe(self, now):
        if now - self.last_probe < 2:
            return None
        self.last_probe = now
        token = secrets.token_urlsafe(12)
        self.pending = (token, now)
        return token

    def acknowledge(self, token, now):
        if not self.pending or not isinstance(token, str) or token != self.pending[0]:
            return False
        sent = self.pending[1]
        self.pending = None
        elapsed = now - sent
        if not 0 <= elapsed <= 5:
            return False
        self.samples.append((now, elapsed))
        return True

    def estimate(self, now):
        recent = [rtt for stamp, rtt in self.samples if now - stamp <= SAMPLE_TTL]
        return min(recent) if recent else 0.
