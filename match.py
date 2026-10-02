from app_paths import config_path as player_config, presets_path, data_path, sounds_path
"""Session orchestration: unchanged card engine plus authoritative clocks and history."""
import json
import math
from pathlib import Path
import time
import uuid
import re7_21 as engine
from cards import CARDS
from presentation_rules import enabled_cards

DEFAULT_TIMER = dict(enabled=False, mode='turn', turn_seconds=30., round_seconds=120., initial_minutes=3., increment_seconds=3., settlement_seconds=3., preparation_seconds=30.)
UNLIMITED_TIMER_FIELDS = {'turn_seconds','round_seconds','initial_minutes','preparation_seconds'}


def timer_config(value):
    config = dict(DEFAULT_TIMER)
    if not isinstance(value, dict):
        return config
    config['enabled'] = value.get('enabled') is True
    config['mode'] = value.get('mode') if value.get('mode') in ('turn', 'round', 'fischer') else 'turn'
    for key, limit in [('turn_seconds', 3600), ('round_seconds', 86400), ('initial_minutes', 1440), ('increment_seconds', 300), ('settlement_seconds', 60), ('preparation_seconds',3600)]:
        number = value.get(key, config[key])
        if number is None and key in UNLIMITED_TIMER_FIELDS:
            config[key]=None
            continue
        if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number):
            config[key] = max(0 if key in ('increment_seconds', 'settlement_seconds') else 1, min(limit, number))
    return config


def load_timer(root):
    try:
        return timer_config(json.loads((player_config(root,'timer.json')).read_text(encoding='utf-8-sig')))
    except (OSError, ValueError):
        return dict(DEFAULT_TIMER)


class Match:
    def __init__(self, timer=None, monotonic=time.monotonic, wall=time.time):
        self.monotonic, self.wall = monotonic, wall
        self.timer = timer_config(timer)
        self.gs = engine.GameState()
        self.gs.last_result = None
        self.gs.end_reason = ''
        self.gs.draw_offer = 0
        self.draw_offered_round = {}
        self.blood_loss = {1: 0, 2: 0}
        self.last_hp = {1: self.gs.p1_fingers, 2: self.gs.p2_fingers}
        self.enabled_cards = enabled_cards(CARDS, engine.WEIGHTS, engine.SETTINGS)
        self.last = monotonic()
        self.log = []
        self.sequence = 0
        self.match_id = uuid.uuid4().hex
        self.reset_clock()
        self.record('round_start')
        self.publish()

    def record(self, event, pid=0, **details):
        if event == 'round_start':
            self.preparation_pending={1:self.gs.round_id==1,2:self.gs.round_id==1}
            self.preparation_remaining={1:self.timer['preparation_seconds'],2:self.timer['preparation_seconds']}
            openings = dict(getattr(self.gs,'opening_cards',{}))
            openings[str(self.gs.round_id)] = [self.gs.p1_hand[0],self.gs.p2_hand[0]]
            self.gs.opening_cards = dict(list(openings.items())[-80:])
            details.update(opening=[[None]+list(self.gs.p1_hand[1:]),[None]+list(self.gs.p2_hand[1:])], target=self.gs.target_score)
        self.sequence += 1
        self.log.append(dict(id=self.sequence, round=self.gs.round_id, event=event, pid=pid, **details))
        self.log = self.log[-300:]

    def reset_clock(self):
        mode = self.timer['mode']
        amount = self.timer['initial_minutes'] if mode=='fischer' else self.timer[mode+'_seconds']
        amount = None if amount is None else float(amount)*(60 if mode=='fischer' else 1)
        self.remaining = {1: amount, 2: amount}

    def publish(self):
        for pid in (1, 2):
            hp = max(0, getattr(self.gs, f'p{pid}_fingers'))
            if self.gs.end_reason not in ('timeout','preparation_timeout'):
                self.blood_loss[pid] += max(0, self.last_hp[pid]-hp)
            self.last_hp[pid] = hp
        self.gs.blood_loss = self.blood_loss.copy()
        self.gs.enabled_cards = self.enabled_cards
        self.gs.clock_config = self.timer.copy()
        self.gs.clock_remaining = self.remaining.copy()
        active=self.timer['enabled'] and self.gs.phase=='ACTION'
        self.gs.clock_active = self.gs.turn if active and not self.preparation_pending[self.gs.turn] else 0
        self.gs.preparation_active=self.gs.turn if active and self.preparation_pending[self.gs.turn] else 0
        self.gs.preparation_remaining=self.preparation_remaining.copy()
        self.gs.action_log = list(self.log)
        self.gs.match_id = self.match_id

    def finish_round(self):
        self.gs.resolve_round()
        self.gs.result_timer = self.wall()+self.timer['settlement_seconds']
        self.gs.draw_offer = 0
        self.gs.last_result = dict(round=self.gs.round_id, hands=[list(self.gs.p1_hand), list(self.gs.p2_hand)], target=self.gs.target_score, winner=self.gs.round_winner, damage=self.gs.round_damage, escape=self.gs.is_escape_end)
        self.record('result', winner=self.gs.round_winner, damage=self.gs.round_damage,
                    totals=[sum(self.gs.p1_hand), sum(self.gs.p2_hand)],
                    hands=[list(self.gs.p1_hand), list(self.gs.p2_hand)], target=self.gs.target_score,
                    hp=[max(0,self.gs.p1_fingers), max(0,self.gs.p2_fingers)])

    def tick(self):
        now = self.monotonic()
        elapsed = max(0, now-self.last)
        self.last = now
        gs = self.gs
        if gs.phase == 'RESULT' and self.wall() >= gs.result_timer:
            if gs.p1_fingers <= 0 or gs.p2_fingers <= 0 or gs.is_escape_end:
                gs.phase = 'GAMEOVER'
                self.record('gameover', winner=gs.round_winner)
            else:
                gs.reset_round()
                if self.timer['mode'] != 'fischer':
                    self.reset_clock()
                self.record('round_start')
            self.publish()
            return
        if gs.phase == 'ACTION' and self.timer['enabled']:
            if self.preparation_pending[gs.turn]:
                left=self.preparation_remaining[gs.turn]
                if left is not None:
                    self.preparation_remaining[gs.turn]=max(0,left-elapsed)
                    if self.preparation_remaining[gs.turn]<=0:
                        pid=gs.turn
                        self.record('preparation_timeout',pid)
                        setattr(gs,f'p{pid}_fingers',0)
                        gs.phase='GAMEOVER';gs.round_winner=3-pid;gs.round_damage=0;gs.end_reason='preparation_timeout'
                        self.record('gameover',winner=3-pid,reason=gs.end_reason)
                self.publish()
                return
            if self.remaining[gs.turn] is not None:
                self.remaining[gs.turn] = max(0, self.remaining[gs.turn]-elapsed)
            for _ in range(2):
                if gs.phase != 'ACTION' or self.preparation_pending[gs.turn] or self.remaining[gs.turn] is None or self.remaining[gs.turn] > 0:
                    break
                pid = gs.turn
                self.record('timeout', pid)
                if self.timer['mode'] == 'fischer':
                    setattr(gs, f'p{pid}_fingers', 0)
                    gs.round_winner = 3-pid
                    gs.round_damage = 0
                    gs.phase = 'GAMEOVER'
                    gs.end_reason = 'timeout'
                    self.record('gameover', winner=3-pid, reason='timeout')
                    break
                self.stay(pid, automatic=True)
        self.publish()

    def handoff(self, pid, automatic=False):
        if self.timer['enabled']:
            if self.timer['mode'] == 'fischer' and not automatic and self.remaining[pid] is not None:
                self.remaining[pid] += self.timer['increment_seconds']
            elif self.timer['mode'] == 'turn':
                self.remaining[3-pid] = self.timer['turn_seconds']
        self.gs.turn = 3-pid

    def stay(self, pid, automatic=False):
        if not automatic:self.preparation_pending[pid]=False
        setattr(self.gs, f'p{pid}_stop', True)
        self.record('stay', pid, automatic=automatic)
        self.handoff(pid, automatic)
        if self.gs.p1_stop and self.gs.p2_stop:
            self.finish_round()
        else:
            self.gs.cleanup_player_instants(self.gs.turn)

    def command(self, pid, message):
        self.tick()  # Timeout wins over a late packet.
        gs = self.gs
        if pid not in (1, 2) or not isinstance(message, str):
            return False
        parts = message.split(':')
        try:
            if int(parts[-1]) != gs.round_id:
                return False
        except (ValueError, IndexError):
            return False
        cmd = parts[0]
        if cmd == 'REMATCH' and gs.phase == 'GAMEOVER' and len(parts) == 2:
            if not getattr(gs, f'p{pid}_req_rematch'):
                setattr(gs, f'p{pid}_req_rematch', True)
                self.record('rematch', pid)
            if gs.p1_req_rematch and gs.p2_req_rematch:
                gs.round_id = 0
                gs.full_reset()
                gs.last_action_time = {1: 0., 2: 0.}
                self.blood_loss = {1: 0, 2: 0}
                self.last_hp = {1: gs.p1_fingers, 2: gs.p2_fingers}
                gs.end_reason = ''
                gs.last_result = None
                gs.draw_offer = 0
                self.draw_offered_round = {}
                self.log = []
                gs.opening_cards = {}
                self.match_id = uuid.uuid4().hex
                self.reset_clock()
                self.last = self.monotonic()
                self.record('round_start')
            self.publish()
            return True
        if cmd in ('SURRENDER', 'DRAW_OFFER', 'DRAW_ACCEPT', 'DRAW_DECLINE'):
            if gs.phase != 'ACTION' or len(parts) != 2:
                return False
            if cmd == 'SURRENDER':
                gs.round_winner, gs.end_reason = 3-pid, 'surrender'
            elif cmd == 'DRAW_OFFER':
                if gs.draw_offer or self.draw_offered_round.get(pid) == gs.round_id:
                    return False
                gs.draw_offer = pid
                self.draw_offered_round[pid] = gs.round_id
                self.record('draw_offer', pid)
                self.publish()
                return True
            else:
                if gs.draw_offer != 3-pid:
                    return False
                if cmd == 'DRAW_DECLINE':
                    gs.draw_offer = 0
                    self.record('draw_decline', pid)
                    self.publish()
                    return True
                gs.round_winner, gs.end_reason = 0, 'agreement'
            gs.phase, gs.round_damage, gs.draw_offer = 'GAMEOVER', 0, 0
            self.record('gameover', pid, winner=gs.round_winner, reason=gs.end_reason)
            self.publish()
            return True
        if gs.phase != 'ACTION' or gs.turn != pid or self.wall()-gs.last_action_time[pid] < .5:
            return False
        trumps = getattr(gs, f'p{pid}_trumps')
        opponent = 3-pid
        if cmd in ('TRUMP', 'DISCARD'):
            if len(parts) != 3:
                return False
            try:
                index = int(parts[1])
            except ValueError:
                return False
            if not 0 <= index < len(trumps):
                return False
            card = trumps[index]
        elif cmd not in ('HIT', 'STAY') or len(parts) != 2:
            return False
        if cmd == 'HIT':
            if gs.check_bust(pid) or any(t['owner'] == opponent and t['type'] in ('GAMBLE', 'SILENCE') for t in gs.active_trumps):
                return False
            before_count = len(getattr(gs, f'p{pid}_hand'))
            gs.draw_card(pid)
            self.preparation_pending[pid]=False
            hand = getattr(gs, f'p{pid}_hand')
            drawn = hand[before_count:] if before_count >= 1 else []
            setattr(gs, f'p{pid}_stop', False)
            self.record('hit', pid, drawn=drawn)  # Only newly exposed cards; never the first hidden card.
            self.handoff(pid)
            gs.cleanup_player_instants(gs.turn)
        elif cmd == 'STAY':
            self.stay(pid)
        elif cmd == 'DISCARD':
            gs.discard_trump(pid, index)
            self.preparation_pending[pid]=False
            setattr(gs, f'p{pid}_stop', False)
            self.record('discard', pid)  # A discarded trump is private.
        elif cmd == 'TRUMP':
            own = [t for t in gs.active_trumps if t['owner'] == pid]
            if any(t['owner'] == opponent and t['type'] == 'DESTROY_BLOCK' for t in gs.active_trumps):
                return False
            if len(own) >= engine.MAX_TABLE_SLOTS:
                if card[1] not in ('SHIELD_ATTACK', 'SHIELD_ATTACK_PLUS', 'OBLIVION') and not (card[1] == 'TARGET' and any(t['type'] == 'TARGET' for t in own)):
                    return False
            old_round = gs.round_id
            self.preparation_pending[pid]=False
            before = [list(gs.p1_hand[1:]), list(gs.p2_hand[1:])]
            target_before = gs.target_score
            locked = any(t['owner'] == opponent and t['type'] in ('GAMBLE','SILENCE') for t in gs.active_trumps)
            gs.use_trump(pid, index)
            self.record('trump', pid, card=card[0], kind=card[1], value=card[2],
                        before=before, after=[list(gs.p1_hand[1:]), list(gs.p2_hand[1:])],
                        target_before=target_before, target=gs.target_score, locked=locked)
            if any(t['owner'] == pid and t['type'] == 'HARVEST' for t in gs.active_trumps):
                gs.give_trump(pid, 1)
            setattr(gs, f'p{opponent}_stop', False)
            if gs.round_id != old_round:
                gs.draw_offer = 0
                if self.timer['mode'] != 'fischer':
                    self.reset_clock()
                self.record('round_start')
        gs.last_action_time[pid] = self.wall()
        self.publish()
        return True
