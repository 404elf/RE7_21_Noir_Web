"""Boundary tests for browser rooms; the original suite tests the shared engine."""
import asyncio
import copy
import json
from pathlib import Path
import random
import sys
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import re7_21 as engine
from match import Match
from cards import CARDS
from rules import rules_active
from web.app import create_app
from web.config import ROOT, load_rules
from web.rooms import Room, RoomError, RoomService

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def app(tmp_path):
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    config["game_settings"]["max_hp"] = 1
    game = tmp_path / "config.json"
    game.write_text(json.dumps(config), encoding="utf-8")
    timer = tmp_path / "timer.json"
    timer.write_text('{"enabled":false,"settlement_seconds":0}', encoding="utf-8")
    return create_app(config_path=game, timer_path=timer)


def wait_state(ws, predicate=lambda s: True):
    for _ in range(100):
        data = ws.receive_json()
        value = consume(ws, data)
        if value is not None and predicate(value):
            return value
    raise AssertionError("Expected state did not arrive")


def consume(ws, data):
    value = getattr(ws, "noir_state", None)
    if data["type"] == "state":
        value = data
    elif data["type"] == "patch":
        assert value and data["base_revision"] == value["revision"]
        value.update(data["changes"], revision=data["revision"])
        for p in data["players"]:
            value["players"][p["id"] - 1].update(p)
        value["events"] = (data["events"] if data["events_reset"]
                           else value.get("events", []) + data["events"])[-80:]
    elif data["type"] != "clock":
        return None
    timing = data.get("timing", data if data["type"] == "clock" else None)
    if timing:
        for p in timing["players"]:
            value["players"][p["id"] - 1].update(p)
        for key in ("paused", "clock_active", "preparation_active", "reconnect_seconds"):
            value[key] = timing[key]
    ws.noir_state = value
    return copy.deepcopy(value)


def authenticate(ws, seat):
    ws.send_json({"type": "auth", "token": seat["token"]})
    return wait_state(ws)


def ready(ws, snapshot):
    ws.send_json({"type": "ready", "revision": snapshot["revision"]})


def room_fixture():
    room = Room("ABCDEFGH", "Host", load_rules(ROOT / "config.json"), {"enabled": False})
    room.join("Guest")
    room.seats[1].socket = room.seats[2].socket = object()
    room.seats[1].ready = room.seats[2].ready = True
    room.advance()
    return room


def act(room, pid, action, **extra):
    room.match.gs.last_action_time[pid] = 0
    room.command(pid, dict(type="action", action=action, revision=room.revision, **extra))


def test_static_service_and_repository_are_separated(app):
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        html = client.get("/")
        assert 'lang="zh-CN"' in html.text
        assert "frame-ancestors 'none'" in html.headers["content-security-policy"]
        assert set(client.get("/api/catalog").json()) == set(CARDS)
        for path in ("/config.json", "/re7_21.py", "/.git/config"):
            assert client.get(path).status_code == 404


@pytest.mark.parametrize("domain", ["game.404elf.dev", "game.other.example"])
@pytest.mark.parametrize("prefix", ["/re7", "/games/noir"])
def test_subpath_and_replacement_domain(domain, prefix, monkeypatch):
    monkeypatch.delenv("NOIR_ORIGIN", raising=False)
    app = create_app(base_path=prefix + "/")
    origin = {"origin": f"https://{domain}"}
    with TestClient(app, base_url=f"https://{domain}") as client:
        redirect = client.get(prefix + "?room=ABCDEFGH", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"] == prefix + "/?room=ABCDEFGH"
        assert client.get(prefix + "/").status_code == 200
        for name in ("app.js", "transport.js", "presentation.js", "style.css", "grain.svg", "favicon.svg"):
            assert client.get(prefix + "/" + name).status_code == 200
            assert client.get("/" + name).status_code == 404
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get(prefix + "/healthz").json() == {"status": "ok"}
        assert set(client.get(prefix + "/api/catalog").json()) == set(CARDS)
        assert client.post("/api/rooms", json={"name": "Wrong path"}, headers=origin).status_code == 404
        host = client.post(prefix + "/api/rooms", json={"name": "Host"}, headers=origin).json()
        code = host["room"]
        guest = client.post(prefix + f"/api/rooms/{code}/join", json={"name": "Guest"}, headers=origin).json()
        with client.websocket_connect(f"wss://{domain}{prefix}/ws/{code}", headers=origin) as a:
            authenticate(a, host)
            with client.websocket_connect(f"wss://{domain}{prefix}/ws/{code}", headers=origin) as b:
                authenticate(b, guest)
                sa = wait_state(a, lambda s: s["players"][1]["connected"])
                ready(a, sa)
                sb = wait_state(b, lambda s: s["players"][0]["ready"])
                ready(b, sb)
                assert wait_state(a, lambda s: s["phase"] == "ACTION")["room"] == code
                assert wait_state(b, lambda s: s["phase"] == "ACTION")["pid"] == 2


def test_subpath_with_https_terminating_proxy(monkeypatch):
    monkeypatch.setenv("NOIR_ORIGIN", "https://game.other.example")
    monkeypatch.setenv("NOIR_BASE_PATH", "/re7")
    with TestClient(create_app(), base_url="http://internal:8000") as client:
        response = client.get("/re7?room=ABCDEFGH", follow_redirects=False)
        assert response.headers["location"] == "/re7/?room=ABCDEFGH"
        endpoint = "/re7/api/rooms"
        assert client.post(endpoint, json={"name": "Host"}, headers={"origin": "https://game.404elf.dev"}).status_code == 403
        host = client.post(endpoint, json={"name": "Host"}, headers={"origin": "https://game.other.example"}).json()
        with client.websocket_connect(f"/re7/ws/{host['room']}", headers={"origin": "https://game.other.example"}) as ws:
            assert authenticate(ws, host)["pid"] == 1


@pytest.mark.parametrize("prefix", ["re7", "/re7//other", "/../re7", "/re7?x=1", "https://example.com/re7"])
def test_invalid_base_path_fails_startup(prefix):
    with pytest.raises(ValueError, match="NOIR_BASE_PATH"):
        create_app(base_path=prefix)


def test_heartbeat_timeout_retains_seat_for_reconnect(app, monkeypatch):
    monkeypatch.setattr("web.app.HEARTBEAT_TIMEOUT", .1)
    with TestClient(app) as client:
        host = client.post("/api/rooms", json={"name": "Host"}).json()
        path = f"/ws/{host['room']}"
        with client.websocket_connect(path, headers=ORIGIN) as ws:
            before = authenticate(ws, host)
            # No ping or action: the server must close recoverably, without a
            # fatal packet that would erase the browser's session credential.
            with pytest.raises(WebSocketDisconnect) as error:
                ws.receive_json()
            assert error.value.code == 1012
        with client.websocket_connect(path, headers=ORIGIN) as ws:
            after = authenticate(ws, host)
            assert after["room"] == before["room"] and after["pid"] == before["pid"]
            assert after["players"][0]["name"] == "Host"


def test_room_reservations_origins_and_bad_input(app):
    with TestClient(app) as client:
        assert client.post("/api/rooms", json={"name": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
        for data in ([], {"name": ""}, {"name": "x" * 21}, {"name": "a\nb"}):
            assert client.post("/api/rooms", json=data).status_code == 400
        assert client.post("/api/rooms", content="x" * 2049, headers={"content-type": "application/json"}).status_code == 400
        host = client.post("/api/rooms", json={"name": "Host"}).json()
        code = host["room"]
        guest = client.post(f"/api/rooms/{code.lower()}/join", json={"name": "Guest"})
        assert guest.status_code == 200
        assert guest.json()["token"] != host["token"]
        assert client.post(f"/api/rooms/{code}/join", json={"name": "Third"}).status_code == 400
        assert client.post("/api/rooms/INVALID/join", json={"name": "Guest"}).status_code == 400
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/{code}", headers={"origin": "https://evil.example"}):
                pass
        with client.websocket_connect(f"/ws/{code}", headers=ORIGIN) as ws:
            ws.send_json({"type": "auth", "token": "invalid"})
            assert ws.receive_json()["type"] == "fatal"


def test_two_websockets_complete_match_reconnect_and_rematch(app):
    with TestClient(app) as client:
        host = client.post("/api/rooms", json={"name": "Host"}).json()
        code = host["room"]
        guest = client.post(f"/api/rooms/{code}/join", json={"name": "Guest"}).json()
        with client.websocket_connect(f"/ws/{code}", headers=ORIGIN) as a:
            authenticate(a, host)
            with client.websocket_connect(f"/ws/{code}", headers=ORIGIN) as b:
                sb = authenticate(b, guest)
                sa = wait_state(a, lambda s: s["players"][1]["connected"])
                ready(a, sa)
                sb = wait_state(b, lambda s: s["players"][0]["ready"])
                ready(b, sb)
                sa = wait_state(a, lambda s: s["phase"] == "ACTION")
                sb = wait_state(b, lambda s: s["phase"] == "ACTION")
                assert sa["players"][1]["hand"][0] is None
                assert sb["players"][0]["hand"][0] is None
                # Wrong-player action and stale action cannot mutate the match.
                b.send_json({"type": "action", "action": "HIT", "revision": sb["revision"], "pid": 1})
                while b.receive_json()["type"] != "error":
                    pass
                # Play actual deals to a health-based end; no surrender shortcut.
                for _ in range(100):
                    if sa["phase"] == "GAMEOVER":
                        break
                    if sa["phase"] != "ACTION":
                        sa = wait_state(a, lambda s: s["phase"] in ("ACTION", "GAMEOVER"))
                        sb = wait_state(b, lambda s: s["revision"] >= sa["revision"])
                        continue
                    pid = sa["turn"]
                    ws = a if pid == 1 else b
                    s = sa if pid == 1 else sb
                    if s["phase"] != "ACTION" or s["turn"] != pid:
                        continue
                    own = s["players"][pid - 1]
                    action = "HIT" if own["total"] < 17 and s["deck_count"] else "STAY"
                    time.sleep(.51)
                    ws.send_json({"type": "action", "action": action, "revision": s["revision"]})
                    sa = wait_state(a, lambda v: v["revision"] > s["revision"])
                    sb = wait_state(b, lambda v: v["revision"] >= sa["revision"])
                else:
                    pytest.fail("Match did not complete")
                assert sa["winner"] in (1, 2)
                assert any(p["hp"] == 0 for p in sa["players"])
                a.send_json({"type": "action", "action": "REMATCH", "revision": sa["revision"]})
                sb = wait_state(b, lambda s: s["players"][0]["rematch"])
                b.send_json({"type": "action", "action": "REMATCH", "revision": sb["revision"]})
                fresh = wait_state(a, lambda s: s["phase"] == "ACTION")
                assert fresh["match_id"] != sa["match_id"]
            paused = wait_state(a, lambda s: s["paused"])
            with client.websocket_connect(f"/ws/{code}", headers=ORIGIN) as b:
                restored = authenticate(b, guest)
                assert restored["match_id"] == fresh["match_id"]
                assert not restored["paused"]
                assert restored["round"] == paused["round"]


def test_projection_hides_private_data_and_allows_only_past_results():
    room = room_fixture()
    gs = room.match.gs
    gs.p1_hand, gs.p2_hand = [3, 4], [8, 9]
    gs.p2_trumps = [("Curse", "CURSE", 0)]
    payload = room.snapshot(1)
    assert payload["players"][1]["hand"] == [None, 9]
    assert payload["players"][1]["total"] is None
    assert payload["players"][1]["trumps"] is None
    for key in ("deck", "opening_cards", "token", "last_action_time"):
        assert key not in payload
    assert "Curse" not in json.dumps(payload["players"])
    room.match.finish_round()
    assert room.snapshot(1)["players"][1]["hand"] == [8, 9]


@pytest.mark.parametrize("index", [-1, 100, True, "0", None])
def test_invalid_indexes_are_rejected_without_mutation(index):
    room = room_fixture()
    before = copy.deepcopy(room.match.gs.p1_trumps)
    with pytest.raises(RoomError):
        act(room, 1, "TRUMP", index=index)
    assert room.match.gs.p1_trumps == before


def test_stale_duplicate_command_and_room_isolation():
    room = room_fixture()
    second = room_fixture()
    revision = room.revision
    act(room, 1, "STAY")
    with pytest.raises(RoomError):
        room.command(1, dict(type="action", action="STAY", revision=revision))
    assert second.match.gs.turn == 1
    assert room.match.gs.turn == 2
    original = engine.SETTINGS
    rules = copy.deepcopy(room.rules)
    rules["game_settings"]["max_hp"] = 37
    with rules_active(rules):
        assert Match().gs.p1_fingers == 37
    assert engine.SETTINGS is original
    assert second.match.gs.p1_fingers == original["max_hp"]


@pytest.mark.parametrize("action", ["SURRENDER", "DRAW_ACCEPT"])
def test_surrender_and_agreed_draw(action):
    room = room_fixture()
    if action == "DRAW_ACCEPT":
        act(room, 2, "DRAW_OFFER")
    act(room, 1, action)
    assert room.match.gs.phase == "GAMEOVER"
    assert room.match.gs.round_winner == (2 if action == "SURRENDER" else 0)


def test_disconnect_freezes_clocks_and_settlement_then_forfeits():
    room = room_fixture()
    gs = room.match.gs
    room.match.timer["enabled"] = True
    room.match.preparation_pending = {1: False, 2: False}
    room.match.remaining = {1: 30., 2: 30.}
    sock = room.seats[2].socket
    room.detach(2, sock)
    remaining = dict(room.match.remaining)
    room.match.last -= 10
    room.advance()
    assert room.match.remaining == remaining
    gs.phase = "RESULT"
    deadline = gs.result_timer = time.time() + 3
    room.match.last -= 5
    room.advance()
    assert gs.result_timer > deadline + 4
    room.missing_since -= room.grace + 1
    room.advance()
    assert gs.phase == "GAMEOVER" and gs.end_reason == "disconnect" and gs.round_winner == 1


def test_noir_command_parity_for_catalog_cards():
    # The browser adapter must leave every engine effect to Match.command.
    # Cover the whole weighted catalog by discovering its actual card tuples.
    import ast
    tree = ast.parse((ROOT / "re7_21.py").read_text(encoding="utf-8"))
    tuples = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple) and len(node.elts) == 3:
            try:
                value = ast.literal_eval(node)
                if isinstance(value[0], str) and isinstance(value[1], str) and isinstance(value[2], int):
                    tuples.add(value)
            except (ValueError, TypeError):
                pass
    assert {card[0] for card in tuples} == set(CARDS)
    for card in tuples:
        room = room_fixture()
        room.match.gs.p1_trumps = [card, ("Shield", "SHIELD", 1), ("Add 1", "ADD", 1)]
        baseline = copy.deepcopy(room.match)
        random.seed(90210)
        with rules_active(room.rules):
            assert baseline.command(1, f"TRUMP:0:{baseline.gs.round_id}")
        random.seed(90210)
        act(room, 1, "TRUMP", index=0)
        for attr in ("p1_hand", "p2_hand", "p1_trumps", "p2_trumps", "deck", "active_trumps", "target_score", "round_id", "turn"):
            assert getattr(room.match.gs, attr) == getattr(baseline.gs, attr), (card, attr)


def test_capacity_and_expiration():
    async def check():
        rules = load_rules(ROOT / "config.json")
        service = RoomService(rules, {}, max_rooms=1, lobby_ttl=.01)
        room = service.create("Host")
        with pytest.raises(RoomError):
            service.create("Extra")
        await asyncio.sleep(.3)
        assert room.closed and not service.rooms
        for _ in range(40):
            service.limit("peer")
        with pytest.raises(RoomError):
            service.limit("peer")
        await service.close()
    asyncio.run(check())


def test_invalid_config_fails_startup(tmp_path):
    path = tmp_path / "config.json"
    for conf in ({"game_settings": {"max_hp": -1}}, {"game_settings": {"deck_range_start": 10, "deck_range_end": 11}}, {"trump_weights": {"Shield": -5}}):
        path.write_text(json.dumps(conf), encoding="utf-8")
        with pytest.raises(ValueError):
            load_rules(path)


def test_deadline_reached_inside_command_still_changes_revision(monkeypatch):
    room = room_fixture()
    before = room.revision
    def expired(pid, message):
        room.match.gs.phase = "GAMEOVER"
        room.match.record("gameover", winner=2, reason="timeout")
        return False
    monkeypatch.setattr(room.match, "command", expired)
    with pytest.raises(RoomError):
        act(room, 1, "HIT")
    assert room.revision > before
    assert room.snapshot(1)["phase"] == "GAMEOVER"


def test_same_seat_cannot_attach_twice_and_non_ascii_token_rejected():
    room = room_fixture()
    with pytest.raises(RoomError):
        room.attach(1, object())
    with pytest.raises(RoomError):
        room.identify("伪造凭证")
    assert room.identify(room.seats[1].token) == 1
