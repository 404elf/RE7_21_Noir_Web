"""Original bot decisions plus browser-room lifecycle and worker boundaries."""
import asyncio
import copy
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import re7_21 as engine
from bot import Strategy, observe, DIFFICULTIES, STYLES
from rules import rules_active
from web.app import create_app
from web.config import ROOT, load_rules
from web.rooms import Room, RoomError, RoomService

AI = dict(difficulty='normal', style='swing')


@pytest.mark.parametrize('difficulty', DIFFICULTIES)
@pytest.mark.parametrize('style', STYLES)
def test_original_strategy_never_receives_opponent_secrets(difficulty, style):
    gs = engine.GameState()
    gs.p2_hand, gs.p1_hand, gs.p2_trumps = [7, 8], [3, 2], []
    before = observe(gs)
    gs.p1_hand[0] = 11
    gs.p1_trumps = [('Escape', 'ESCAPE', 0)] * len(gs.p1_trumps)
    gs.deck.reverse()
    after = observe(gs)
    assert before == after
    assert Strategy(difficulty, style, 42).choose(before) == Strategy(difficulty, style, 42).choose(after)


@pytest.mark.parametrize('difficulty', ['hard', 'nightmare'])
def test_original_planner_recovers_but_respects_blockade(difficulty):
    from dataclasses import replace
    gs = engine.GameState()
    gs.p2_hand, gs.p1_hand = [7, 8, 9], [3, 2]
    gs.p2_trumps = [('Return', 'RETURN', 0)]
    view = observe(gs)
    assert Strategy(difficulty, 'conservative', 0).choose(view) == 'TRUMP:0'
    assert Strategy(difficulty, 'conservative', 0).choose(replace(view, trump_locked=True)) == 'STAY'


@pytest.mark.parametrize('invalid', [None, {}, {'difficulty':'impossible','style':'swing'},
                                    {'difficulty':[],'style':'swing'}, {'difficulty':'easy','style':'swing','seed':1}])
def test_invalid_ai_is_rejected_without_allocating_room(invalid):
    app = create_app()
    with TestClient(app) as client:
        assert client.post('/api/rooms', json=dict(name='Host', ai=invalid), headers={'origin':'http://testserver'}).status_code == 400
        assert not app.state.rooms.rooms


class Socket:
    async def send_json(self, value):
        pass

    async def close(self, **kwargs):
        pass


def test_solo_settings_seat_privacy_disconnect_and_rematch():
    rules = load_rules(ROOT / 'config.json')
    clock = [0.]
    room = Room('ABCDEFGH', 'Host', rules, {'enabled':False}, ai=AI, clock=lambda:clock[0], grace=1)
    assert room.snapshot(1)['players'][1]['ready']
    with pytest.raises(RoomError):
        room.identify(room.seats[2].token)
    with pytest.raises(RoomError):
        room.join('Unexpected guest')
    room.configure(1, room.revision, rules, {'enabled':False})
    assert room.seats[2].ready and not room.seats[1].ready
    socket = Socket()
    room.attach(1, socket)
    room.command(1, dict(type='ready', revision=room.revision))
    assert room.match and room.connected == 2
    projection = room.snapshot(1)
    assert projection['players'][1]['hand'][0] is None
    assert projection['players'][1]['trumps'] is None
    room.detach(1, socket)
    assert room.snapshot(1)['paused']
    clock[0] += 2
    room.advance()
    assert room.match.gs.phase == 'GAMEOVER' and room.match.gs.round_winner == 2


def test_actual_worker_full_match_multiple_rooms_and_rematch():
    async def play():
        rules = load_rules(ROOT / 'config.json')
        rules['game_settings'].update(max_hp=1, initial_trumps_count=0, round_reward_trumps_count=0, hit_draw_trump_probability=0)
        timer = dict(enabled=False, settlement_seconds=0)
        service = RoomService(rules, timer, max_ai_rooms=2)
        rooms = [service.create('Human', ai=dict(difficulty=d,style='conservative')) for d in ['hard','nightmare']]
        try:
            with pytest.raises(RoomError):
                service.create('Third', ai=AI)
            multiplayer = service.create('Friends')
            assert multiplayer.ai is None and multiplayer.join('Guest') == 2
            for room in rooms:
                room.attach(1, Socket())
                room.command(1, dict(type='ready', revision=room.revision))
            before_settings = copy.deepcopy(engine.SETTINGS)
            deadline = asyncio.get_running_loop().time() + 20
            while any(r.match.gs.phase != 'GAMEOVER' for r in rooms):
                assert asyncio.get_running_loop().time() < deadline, 'AI failed to finish the match'
                for room in rooms:
                    gs = room.match.gs
                    if gs.phase == 'ACTION' and gs.turn == 1:
                        room.command(1, dict(type='action', action='STAY', revision=room.revision))
                await asyncio.sleep(.03)
            # Planner simulations stay in workers; real HITs use engine RNG.
            assert engine.SETTINGS == before_settings
            first = rooms[0]
            old_match = first.match.match_id
            first.command(1, dict(type='action', action='REMATCH', revision=first.revision))
            await asyncio.sleep(.2)
            assert first.match.match_id != old_match and first.match.gs.phase == 'ACTION'
        finally:
            await service.close()
    asyncio.run(play())


def test_stale_worker_result_does_not_play_after_disconnect():
    async def check():
        from concurrent.futures import Future
        class HeldExecutor:
            def submit(self, *args):
                self.future = Future()
                return self.future
            def shutdown(self, **kwargs):
                pass
        rules = load_rules(ROOT / 'config.json')
        service = RoomService(rules, dict(enabled=False))
        service.ai_pool = HeldExecutor()
        room = service.create('Human', ai=AI)
        socket = Socket()
        room.attach(1, socket)
        room.command(1, dict(type='ready', revision=room.revision))
        room.command(1, dict(type='action', action='STAY', revision=room.revision))
        service.schedule_bot(room)
        await asyncio.sleep(0)
        assert not service.ai_pool.future.done()
        room.detach(1, socket)
        frozen = copy.deepcopy(room.match.gs.p2_hand)
        service.ai_pool.future.set_result(('HIT', room.strategy))
        await room.bot_task
        assert room.match.gs.p2_hand == frozen and room.snapshot(1)['paused']
        await service.close()
    asyncio.run(check())
