import copy
import json
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from match import Match, timer_config
import re7_21 as engine


class Clock:
    def __init__(self):
        self.now = 100000.
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class MatchTests(unittest.TestCase):
    def make(self, **config):
        clock = Clock()
        match = Match(config, monotonic=clock, wall=clock)
        return match, clock

    def test_disabled_and_turn_timeout_late_packet(self):
        match, clock = self.make(enabled=False)
        clock.advance(999)
        match.tick()
        self.assertEqual(match.gs.turn, 1)
        match, clock = self.make(enabled=True, mode='turn', turn_seconds=5)
        match.preparation_pending={1:False,2:False}  # Test the running turn clock, after preparation.
        clock.advance(5.1)
        self.assertFalse(match.command(1, f'HIT:{match.gs.round_id}'))
        self.assertTrue(match.gs.p1_stop)
        self.assertEqual(match.gs.turn, 2)
        self.assertEqual(match.remaining[2], 5)
        self.assertIn('timeout', [e['event'] for e in match.log])

    def test_fischer_increment_only_on_handoff_and_match_timeout(self):
        match, clock = self.make(enabled=True, mode='fischer', initial_minutes=3, increment_seconds=3)
        match.gs.p1_trumps = [('Shield', 'SHIELD', 1)]
        clock.advance(2)
        self.assertTrue(match.command(1, f'DISCARD:0:{match.gs.round_id}'))
        self.assertEqual(match.remaining[1], 180)  # First discard uses preparation, no increment.
        clock.advance(1)
        self.assertTrue(match.command(1, f'STAY:{match.gs.round_id}'))
        self.assertEqual(match.remaining[1], 182)
        match.preparation_pending[2]=False
        clock.advance(183)
        match.tick()
        self.assertEqual(match.gs.phase, 'GAMEOVER')
        self.assertEqual(match.gs.round_winner, 1)
        self.assertEqual(match.gs.p2_fingers, 0)

    def test_round_budget_not_reset_on_turn_and_settlement_pauses(self):
        match, clock = self.make(enabled=True, mode='round', round_seconds=10, settlement_seconds=10)
        match.preparation_pending={1:False,2:False}  # Budget behavior after both first moves.
        match.gs.p1_hand = [1, 2]
        clock.advance(4)
        match.command(1, f'HIT:{match.gs.round_id}')
        clock.advance(3)
        match.command(2, f'STAY:{match.gs.round_id}')
        self.assertEqual(match.remaining, {1: 6, 2: 7})
        clock.advance(7)
        match.tick()
        self.assertEqual(match.gs.phase, 'RESULT')
        saved = match.remaining.copy()
        clock.advance(5)
        match.tick()
        self.assertEqual(saved, match.remaining)
        match.gs.result_timer = clock.now-1
        match.tick()
        self.assertEqual(match.remaining, {1: 10, 2: 10})

    def test_no_extra_increment_for_trump_and_round_reset_keeps_fischer_bank(self):
        match, clock = self.make(enabled=True, mode='fischer')
        match.gs.p1_trumps = [('Oblivion', 'OBLIVION', 0)]
        clock.advance(8)
        rid = match.gs.round_id
        match.command(1, f'TRUMP:0:{rid}')
        self.assertGreater(match.gs.round_id, rid)
        self.assertEqual(match.remaining[1], 180)  # First trump consumes preparation only.
        self.assertEqual(match.remaining[2], 180)

    def test_rematch_resets_clocks_identity_and_history(self):
        match, clock = self.make(enabled=True, mode='fischer')
        old = match.match_id
        clock.advance(181)
        match.tick()
        match.command(1, f'REMATCH:{match.gs.round_id}')
        match.command(2, f'REMATCH:{match.gs.round_id}')
        self.assertEqual(match.gs.phase, 'ACTION')
        self.assertEqual(match.remaining, {1: 180, 2: 180})
        self.assertNotEqual(old, match.match_id)
        self.assertEqual([e['event'] for e in match.log], ['round_start'])

    def test_rejected_commands_are_not_logged_and_discards_hide_identity(self):
        match, clock = self.make()
        match.gs.p1_trumps = [('Curse', 'CURSE', 0)]
        before = len(match.log)
        for message in [f'TRUMP:-1:{match.gs.round_id}', 'HIT:9999', 'HIT:extra:1']:
            self.assertFalse(match.command(1, message))
        self.assertEqual(len(match.log), before)
        match.command(1, f'DISCARD:0:{match.gs.round_id}')
        self.assertNotIn('Curse', json.dumps(match.log))
        self.assertNotIn('hand', json.dumps(match.log))

    def test_trump_effects_equal_unchanged_engine(self):
        cards = [('Return', 'RETURN', 0), ('Perfect', 'PERFECT', 0), ('Change', 'CHANGE', 0),
                 ('Trump+', 'TRUMP_EXCHANGE', 0), ('Curse', 'CURSE', 0), ('Oblivion', 'OBLIVION', 0)]
        for card in cards:
            match, clock = self.make()
            match.gs.p1_trumps = [card, ('Shield', 'SHIELD', 1), ('Add 1', 'ADD', 1)]
            baseline = copy.deepcopy(match.gs)
            random.seed(456)
            baseline.use_trump(1, 0)
            baseline.p2_stop = False
            random.seed(456)
            self.assertTrue(match.command(1, f'TRUMP:0:{match.gs.round_id}'))
            for attr in ('p1_hand', 'p2_hand', 'p1_trumps', 'p2_trumps', 'deck', 'target_score', 'active_trumps'):
                self.assertEqual(getattr(baseline, attr), getattr(match.gs, attr), (card, attr))

    def test_timer_validation(self):
        config = timer_config(dict(enabled=True, mode='invalid', initial_minutes=float('nan'), increment_seconds=-4))
        self.assertEqual(config['mode'], 'turn')
        self.assertEqual(config['initial_minutes'], 3)
        self.assertEqual(config['increment_seconds'], 0)

    def test_default_match_retains_desktop_action_interval(self):
        match, clock = self.make(enabled=False)
        match.gs.p1_trumps = [('Shield', 'SHIELD', 1)] * 3
        message = f'DISCARD:0:{match.gs.round_id}'
        self.assertTrue(match.command(1, message))
        self.assertFalse(match.command(1, message))
        clock.advance(.49)
        self.assertFalse(match.command(1, message))
        clock.advance(.02)
        self.assertTrue(match.command(1, message))
