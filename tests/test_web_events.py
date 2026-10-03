"""Delivery regressions: idle traffic, privacy, gaps, clocks and reconnects."""
import asyncio
import copy
import json
from types import SimpleNamespace

from test_web import act, consume, room_fixture
from web.config import ROOT, load_rules
from web.rooms import Room


def test_bet_preview_matches_effects_without_revealing_add21_hidden_condition():
    room = room_fixture()
    gs = room.match.gs
    gs.active_trumps = [dict(name='Add 1', type='ADD', owner=1, val=1)]
    assert [p['stake'] for p in room.snapshot(1)['players']] == [1, 2]
    gs.active_trumps = [dict(name='Add21', type='ADD_21', owner=1, val=21)]
    gs.p1_hand = [10, 11]
    before = room.snapshot(2)
    assert before['players'][1]['stake'] is None
    assert room.snapshot(1)['players'][1]['stake'] == 22
    gs.p1_hand = [1, 11]  # Only the private card changes.
    assert room.snapshot(2) == before
    assert room.snapshot(1)['players'][1]['stake'] == 1
    gs.phase = 'RESULT'
    assert room.snapshot(2)['players'][1]['stake'] == 1


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, value):
        self.messages.append(copy.deepcopy(value))


def test_idle_room_sends_nothing_and_actions_send_private_patches():
    async def run():
        room = room_fixture()
        sockets = [Socket(), Socket()]
        for pid, socket in enumerate(sockets, 1):
            room.seats[pid].socket = socket
        await room.broadcast()
        caches = [SimpleNamespace(noir_state=copy.deepcopy(s.messages[0])) for s in sockets]
        full_size = sum(len(json.dumps(s.messages[0]).encode()) for s in sockets)
        for s in sockets:
            s.messages.clear()
        for _ in range(40):
            room.advance()
            await room.broadcast()
        assert not any(s.messages for s in sockets)
        pid = room.match.gs.turn
        act(room, pid, "DISCARD", index=0)
        await asyncio.gather(room.broadcast(), room.broadcast())
        for number, socket in enumerate(sockets, 1):
            assert len(socket.messages) == 1
            patch = socket.messages[0]
            assert patch["type"] == "patch"
            assert "rules" not in patch["changes"] and "timer" not in patch["changes"]
            assert len(patch["events"]) == 1 and patch["events"][0]["event"] == "discard"
            rebuilt = consume(caches[number - 1], patch)
            expected = room.snapshot(number)
            assert {key: rebuilt[key] for key in expected} == expected
            opponent = rebuilt["players"][2 - number]
            assert opponent["hand"][0] is None and opponent["trumps"] is None
        patch_size = sum(len(json.dumps(s.messages[0]).encode()) for s in sockets)
        assert patch_size < full_size / 2
        await room.deliver(1, sockets[0], force=True)
        assert sockets[0].messages[-1]["type"] == "state"
        assert sockets[0].messages[-1]["rules"] == room.rules
    asyncio.run(run())


def test_clock_calibration_is_small_and_at_most_once_per_five_seconds():
    async def run():
        now = [100.]
        room = Room("ABCDEFGH", "Host", load_rules(ROOT / "config.json"),
                    {"enabled": True, "mode": "turn", "turn_seconds": 30}, clock=lambda: now[0])
        room.join("Guest")
        sockets = [Socket(), Socket()]
        for pid, socket in enumerate(sockets, 1):
            room.seats[pid].socket = socket
            room.seats[pid].ready = True
        room.advance()
        await room.broadcast()
        for s in sockets:
            s.messages.clear()
        for _ in range(19):
            now[0] += .25
            await room.broadcast()
        assert not any(s.messages for s in sockets)
        now[0] += .25
        await room.broadcast()
        for s in sockets:
            assert len(s.messages) == 1
            assert s.messages[0]["type"] == "clock"
            assert not {"rules", "events", "hand", "trumps"} & s.messages[0].keys()
            assert len(json.dumps(s.messages[0])) < 600
    asyncio.run(run())


def test_old_socket_cannot_overwrite_the_reconnected_seat_baseline():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowSocket(Socket):
            async def send_json(self, value):
                entered.set()
                await release.wait()
                await super().send_json(value)
        room = room_fixture()
        old, new = SlowSocket(), Socket()
        room.seats[1].socket = old
        pending = asyncio.create_task(room.deliver(1, old))
        await entered.wait()
        room.detach(1, old)
        room.attach(1, new)
        release.set()
        await pending
        await room.deliver(1, new)
        assert len(new.messages) == 1 and new.messages[0]["type"] == "state"
        assert new.messages[0]["revision"] == room.revision
    asyncio.run(run())


def test_snapshot_is_frozen_before_a_slow_network_send():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowSocket(Socket):
            async def send_json(self, value):
                # Starlette serializes JSON before awaiting its network send.
                self.messages.append(copy.deepcopy(value))
                entered.set()
                await release.wait()
        room = room_fixture()
        socket = SlowSocket()
        room.seats[1].socket = socket
        room.match.gs.active_trumps = [{"name": "Shield", "type": "SHIELD", "owner": 1, "val": 2, "counter": 1}]
        pending = asyncio.create_task(room.deliver(1, socket))
        await entered.wait()
        room.match.gs.active_trumps[0]["counter"] = 2
        room.changed()
        release.set()
        await pending
        assert socket.messages[0]["active_trumps"][0]["counter"] == 1
        await room.deliver(1, socket)
        assert socket.messages[1]["changes"]["active_trumps"][0]["counter"] == 2
    asyncio.run(run())
