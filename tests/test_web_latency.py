"""Deadline fairness, bounded compensation and independent slow-peer delivery."""
import asyncio
import copy

import pytest

from test_match_core import Clock
from test_web_events import Socket
from test_web import ORIGIN, authenticate, app
from fastapi.testclient import TestClient
from web.config import ROOT, load_rules
from web.latency import LagMeter
from web.rooms import Room, RoomError


def make_room(mode='turn', *, preparation=False, rtt=.35):
    clock = Clock()
    room = Room('ABCDEFGH', 'Host', load_rules(ROOT/'config.json'),
                dict(enabled=True, mode=mode, turn_seconds=5, round_seconds=5,
                     initial_minutes=1, increment_seconds=3, preparation_seconds=5), clock=clock)
    room.join('Guest')
    for seat in room.seats.values():
        seat.socket, seat.ready = Socket(), True
    room.advance()
    room.match.wall = clock
    room.match.preparation_pending = {1: preparation, 2: preparation}
    room.match.gs.p1_hand = room.match.gs.p2_hand = [1, 2]
    room.match.gs.p1_trumps = [('Shield', 'SHIELD', 1)]*10
    for seat in room.seats.values():
        if rtt is not None:
            seat.latency.samples.append((clock.now, rtt))
    return room, clock


def move(room, action='DISCARD', think=0, **extra):
    room.command(1, dict(type='action', action=action, index=0,
                         revision=room.revision, think_ms=think, **extra))


def test_nonce_measurement_replay_expiry_and_delayed_pong_cannot_raise_baseline():
    meter = LagMeter()
    nonce = meter.probe(100)
    assert not meter.acknowledge('fake', 100.1)
    assert meter.acknowledge(nonce, 100.2)
    assert not meter.acknowledge(nonce, 100.3)
    assert meter.probe(101) is None
    nonce = meter.probe(103)
    assert meter.acknowledge(nonce, 104)
    assert meter.estimate(104) == pytest.approx(.2)
    assert meter.estimate(135) == 0
    nonce = meter.probe(136)
    assert not meter.acknowledge(nonce, 142)
    meter.reset_connection()
    assert meter.estimate(142) == 0


@pytest.mark.parametrize('mode', ['turn', 'round', 'fischer'])
def test_only_wire_time_refunded_and_original_fischer_increment_unchanged(mode):
    room, clock = make_room(mode)
    original = room.match.remaining[1]
    clock.advance(2)
    move(room, think=1700)
    assert room.match.remaining[1] == pytest.approx(original-1.7)
    assert room.match.last_lag_credit == pytest.approx(.3)
    assert room.seats[1].lag_budget == pytest.approx(.7)
    clock.advance(.7)
    move(room, 'STAY', think=400)
    assert room.match.remaining[1] == pytest.approx(original-2.1+(3 if mode=='fischer' else 0))
    assert room.match.gs.turn == 2


@pytest.mark.parametrize('preparation', [False, True])
@pytest.mark.parametrize('mode', ['turn', 'round', 'fischer'])
def test_packet_sent_before_deadline_survives_measured_inflight_grace(preparation, mode):
    room, clock = make_room(mode, preparation=preparation)
    amount = room.match.preparation_remaining[1] if preparation else room.match.remaining[1]
    clock.advance(amount+.1)
    room.seats[1].latency.samples.append((clock.now, .35))
    room.advance()
    assert room.match.gs.phase == 'ACTION' and room.match.gs.turn == 1
    assert room.snapshot(1)['players'][0]['preparation' if preparation else 'clock'] == 0
    move(room, 'STAY', think=round((amount-.2)*1000))
    assert room.match.gs.turn == 2
    assert not any(e['event'].endswith('timeout') for e in room.match.log)
    bucket = room.match.preparation_remaining if preparation else room.match.remaining
    assert bucket[1] == pytest.approx(.2+(3 if mode=='fischer' and not preparation else 0))


@pytest.mark.parametrize('preparation', [False, True])
def test_idle_timeout_is_bounded_and_late_packet_cannot_revive_it(preparation):
    room, clock = make_room(preparation=preparation)
    clock.advance(5.36)
    room.advance()
    before = copy.deepcopy(room.match.log)
    with pytest.raises(RoomError):
        move(room, 'STAY', think=0)
    assert room.match.log == before
    assert any(e['event'].endswith('timeout') for e in before)


def test_claimed_thought_at_or_after_deadline_gets_no_extra_life():
    room, clock = make_room()
    clock.advance(5.1)
    with pytest.raises(RoomError):
        move(room, 'STAY', think=5000)
    assert room.match.gs.p1_stop and room.match.gs.turn == 2
    assert room.seats[1].lag_budget == 1


def test_forged_ping_and_thought_cannot_exceed_server_cap_or_per_turn_budget():
    room, clock = make_room(rtt=2)
    for _ in range(4):
        clock.advance(.6)
        move(room, think=0, rtt_ms=999999, lag_credit=999999)
    assert room.match.remaining[1] == pytest.approx(5-2.4+1)
    assert room.seats[1].lag_budget == 0
    assert room.lag_allowance(1) == 0
    assert room.match.gs.turn == 1
    assert room.match.gs.last_action_time[1] == clock.now


def test_unmeasured_client_gets_no_credit_and_refresh_does_not_refill_budget():
    room, clock = make_room(rtt=None)
    clock.advance(1)
    move(room, think=0, rtt_ms=100000)
    assert room.match.remaining[1] == 4
    room.seats[1].latency.samples.append((clock.now, .35))
    clock.advance(.6)
    move(room, think=0)
    saved = room.seats[1].lag_budget
    old = room.seats[1].socket
    room.detach(1, old)
    clock.advance(10)
    room.attach(1, Socket())
    room.seats[1].latency.samples.append((clock.now, .35))
    assert room.seats[1].lag_budget == saved
    assert room.lag_allowance(1) <= saved
    assert room.match.clock_debit[1] == pytest.approx(0)


def test_rejected_stale_duplicate_and_cooldown_moves_never_refund_time():
    room, clock = make_room()
    clock.advance(1)
    room.match.gs.active_trumps = [dict(owner=2, type='DESTROY_BLOCK', val=1)]
    with pytest.raises(RoomError):
        move(room, 'TRUMP')
    assert room.match.remaining[1] == 4 and room.seats[1].lag_budget == 1
    saved_revision = room.revision
    move(room)
    before = (room.match.remaining[1], room.seats[1].lag_budget, len(room.match.log))
    with pytest.raises(RoomError):
        move(room)
    with pytest.raises(RoomError):
        room.command(1, dict(type='action', action='DISCARD', index=0, revision=saved_revision, think_ms=0))
    assert (room.match.remaining[1], room.seats[1].lag_budget, len(room.match.log)) == before


@pytest.mark.parametrize('think', [True, -1, 1.5, '0', 86400001])
def test_malformed_timing_cannot_change_cards_or_earn_credit(think):
    room, clock = make_room()
    clock.advance(1)
    with pytest.raises(RoomError):
        move(room, think=think)
    assert len(room.match.gs.p1_trumps) == 10 and room.seats[1].lag_budget == 1


def test_slow_peer_does_not_hold_sender_responses_or_timeout_polling():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowSocket(Socket):
            async def send_json(self, value):
                entered.set()
                await release.wait()
                await super().send_json(value)
            async def close(self, **kwargs):
                pass
        room, clock = make_room()
        await room.broadcast()
        slow = SlowSocket()
        room.seats[2].socket = slow
        room.changed()
        room.notify(exclude=1)
        await entered.wait()
        clock.advance(.6)
        move(room)
        result = {'command_id': 1, 'processing_ms': .1, 'compensated_ms': 350}
        await asyncio.wait_for(room.deliver(1, room.seats[1].socket, action_result=result), .1)
        assert room.seats[1].socket.messages[-1]['action_result'] == result
        room.notify(exclude=1)
        assert len(room.notifications) == 1
        clock.advance(6)
        room.advance()
        assert room.match.gs.turn == 2
        release.set()
        await room.stop_notifications()
    asyncio.run(run())


def test_receipt_not_lost_when_background_delivery_already_sent_the_patch():
    async def run():
        room, clock = make_room()
        await room.broadcast()
        clock.advance(1)
        move(room)
        socket = room.seats[1].socket
        await room.deliver(1, socket)
        result = {'command_id': 1, 'processing_ms': .1, 'compensated_ms': 350}
        await room.deliver(1, socket, action_result=result)
        assert socket.messages[-1]['type'] == 'clock'
        assert socket.messages[-1]['action_result'] == result
    asyncio.run(run())


def test_budget_refills_on_a_real_handoff_not_each_trump_or_clock_message():
    room, clock = make_room(rtt=2)
    for _ in range(3):
        clock.advance(.6)
        move(room)
    assert room.seats[1].lag_budget == 0
    clock.advance(.6)
    move(room, 'STAY')
    assert room.lag_allowance(1) == 0
    clock.advance(.6)
    room.command(2, dict(type='action',action='HIT',revision=room.revision,think_ms=0))
    assert room.match.gs.turn == 1
    assert room.lag_allowance(1) == .5
    assert room.seats[1].lag_budget == 1


def test_unlimited_and_disabled_clocks_never_create_refund_time():
    room, clock = make_room()
    room.match.remaining[1] = None
    clock.advance(3)
    move(room)
    assert room.match.remaining[1] is None and room.match.last_lag_credit == 0
    room.match.timer['enabled'] = False
    clock.advance(3)
    move(room)
    assert room.match.last_lag_credit == 0 and room.seats[1].lag_budget == 1


def test_authenticated_nonce_exchange_and_basic_ping_compatibility(app):
    with TestClient(app) as client:
        seat=client.post('/api/rooms',json={'name':'Host'}).json()
        with client.websocket_connect('/ws/'+seat['room'],headers=ORIGIN) as ws:
            authenticate(ws,seat)
            ws.send_json({'type':'ping','measure':True})
            pong=ws.receive_json()
            assert pong['type']=='pong' and isinstance(pong['probe'],str)
            ws.send_json({'type':'probe_ack','probe':pong['probe']})
            sample=ws.receive_json()
            assert sample['type']=='network' and 0<=sample['rtt_ms']<5000
            ws.send_json({'type':'probe_ack','probe':pong['probe']})
            ws.send_json({'type':'ping'})
            assert ws.receive_json()=={'type':'pong'}
