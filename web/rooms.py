"""Authoritative browser rooms. All engine mutations run synchronously on one loop."""
import asyncio
import copy
from collections import deque
from dataclasses import dataclass, field
import secrets
import time

from starlette.websockets import WebSocketDisconnect

from match import Match
from rules import rules_active
from web.protocol import make_patch

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ACTIONS = {"HIT", "STAY", "TRUMP", "DISCARD", "SURRENDER", "DRAW_OFFER",
           "DRAW_ACCEPT", "DRAW_DECLINE", "REMATCH"}


class RoomError(ValueError):
    pass


@dataclass
class Seat:
    name: str
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    socket: object = None
    ready: bool = False
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    view: dict = None
    clock_sent: float = 0

    async def send(self, socket, value):
        async with self.send_lock:
            await asyncio.wait_for(socket.send_json(value), 2)


class Room:
    def __init__(self, code, name, rules, timer, *, grace=60, clock=time.monotonic):
        self.code = code
        self.rules, self.timer = copy.deepcopy(rules), copy.deepcopy(timer)
        self.clock, self.grace = clock, grace
        self.seats = {1: Seat(name)}
        self.match = None
        self.revision = 0
        self.created = self.updated = clock()
        self.missing_since = None
        self.closed = False
        self.task = None

    def changed(self):
        self.revision += 1
        self.updated = self.clock()

    @property
    def connected(self):
        return sum(s.socket is not None for s in self.seats.values())

    def identify(self, token):
        if not isinstance(token, str) or not token.isascii() or len(token) > 100:
            raise RoomError("房间凭证无效，请重新建房或加入。")
        for pid, seat in self.seats.items():
            if secrets.compare_digest(seat.token, token):
                return pid
        raise RoomError("房间凭证无效，请重新建房或加入。")

    def join(self, name):
        if self.closed or 2 in self.seats or self.match:
            raise RoomError("房间已满或已开始。")
        self.seats[2] = Seat(name)
        self.changed()
        return 2

    def attach(self, pid, socket):
        if self.seats[pid].socket is not None:
            raise RoomError("该席位已在另一页面连接。请先关闭原页面。")
        # Freeze up to this instant before restoring the second connection.
        self.advance()
        self.seats[pid].socket = socket
        self.seats[pid].view = None
        self.changed()
        self.advance()

    def detach(self, pid, socket):
        if self.seats[pid].socket is socket:
            self.advance()
            self.seats[pid].socket = None
            self.seats[pid].view = None
            if not self.match:
                self.seats[pid].ready = False
            self.changed()
            self.advance()

    def advance(self):
        now = self.clock()
        if not self.match:
            if self.connected == 2 and all(s.ready for s in self.seats.values()):
                with rules_active(self.rules):
                    self.match = Match(self.timer)
                self.changed()
            return
        match, gs = self.match, self.match.gs
        if self.connected < 2 and gs.phase != "GAMEOVER":
            if self.missing_since is None:
                self.missing_since = now
            elapsed = max(0, match.monotonic() - match.last)
            match.last = match.monotonic()
            if gs.phase == "RESULT":
                gs.result_timer += elapsed
            if now - self.missing_since >= self.grace:
                present = [p for p, s in self.seats.items() if s.socket is not None]
                gs.phase, gs.end_reason = "GAMEOVER", "disconnect"
                gs.round_winner = present[0] if len(present) == 1 else 0
                gs.round_damage, gs.draw_offer = 0, 0
                match.record("gameover", winner=gs.round_winner, reason="disconnect")
                match.publish()
                self.changed()
            return
        self.missing_since = None
        before = (match.sequence, gs.phase, gs.round_id)
        with rules_active(self.rules):
            match.tick()
        if before != (match.sequence, gs.phase, gs.round_id):
            self.changed()

    def command(self, pid, value):
        self.advance()
        if not isinstance(value, dict) or type(value.get("revision")) is not int:
            raise RoomError("操作格式无效。")
        if value["revision"] != self.revision:
            raise RoomError("牌桌已更新，请按当前状态重新操作。")
        if value.get("type") == "ready" and not self.match:
            self.seats[pid].ready = True
            self.changed()
            self.advance()
            return
        if value.get("type") != "action" or not self.match or self.connected != 2:
            raise RoomError("请等待双方连接并准备。")
        action = value.get("action")
        if not isinstance(action, str) or action not in ACTIONS:
            raise RoomError("未知操作。")
        gs = self.match.gs
        parts = [action]
        if action in ("TRUMP", "DISCARD"):
            index = value.get("index")
            if type(index) is not int or not 0 <= index < len(getattr(gs, f"p{pid}_trumps")):
                raise RoomError("王牌位置无效。")
            parts.append(str(index))
        parts.append(str(gs.round_id))
        before = self.match.sequence
        with rules_active(self.rules):
            accepted = self.match.command(pid, ":".join(parts))
        if not accepted:
            if before != self.match.sequence:
                self.changed()  # Match.command also ticks: a deadline may have just elapsed.
            raise RoomError("当前不能执行：请检查行动方、封锁效果和 0.5 秒操作间隔。")
        self.changed()

    def configure(self, pid, revision, rules, timer):
        self.advance()
        if pid != 1:
            raise RoomError("只有房主可以修改房间规则。")
        if self.match:
            raise RoomError("对局已开始，规则已锁定。请另建房间使用新规则。")
        if type(revision) is not int or revision != self.revision:
            raise RoomError("房间已更新，请查看最新规则后重试。")
        self.rules, self.timer = copy.deepcopy(rules), copy.deepcopy(timer)
        for seat in self.seats.values():
            seat.ready = False
        self.changed()

    def snapshot(self, pid):
        """An explicit projection, never vars(gs) / wire.encode(gs)."""
        gs = self.match.gs if self.match else None
        reveal = gs is not None and gs.phase in ("RESULT", "GAMEOVER")
        paused = bool(gs and gs.phase != "GAMEOVER" and self.connected < 2)
        players = []
        for p in (1, 2):
            seat = self.seats.get(p)
            player = dict(id=p, name=seat.name if seat else "等待玩家", connected=bool(seat and seat.socket),
                          ready=bool(seat and seat.ready))
            if gs:
                hand = list(getattr(gs, f"p{p}_hand"))
                player.update(hp=max(0, getattr(gs, f"p{p}_fingers")),
                              hand=hand if p == pid or reveal else [None] + hand[1:],
                              total=sum(hand) if p == pid or reveal else None,
                              trumps=list(getattr(gs, f"p{p}_trumps")) if p == pid else None,
                              trump_count=len(getattr(gs, f"p{p}_trumps")),
                              stopped=getattr(gs, f"p{p}_stop"),
                              rematch=getattr(gs, f"p{p}_req_rematch"),
                              clock=self.match.remaining[p], preparation=self.match.preparation_remaining[p])
            players.append(player)
        value = dict(type="state", room=self.code, revision=self.revision, pid=pid, players=players,
                     phase=gs.phase if gs else "LOBBY", paused=paused, rules=self.rules, timer=self.timer,
                     settings_locked=self.match is not None,
                     reconnect_seconds=max(0, int(self.grace - (self.clock() - self.missing_since)))
                     if paused and self.missing_since is not None else None)
        if gs:
            # No deck contents, future randomness, opening_cards or opponent held trumps.
            value.update(round=gs.round_id, turn=gs.turn, target=gs.target_score, max_hp=gs.max_hp_limit,
                         deck_count=len(gs.deck), active_trumps=list(gs.active_trumps),
                         winner=gs.round_winner, damage=gs.round_damage, end_reason=gs.end_reason,
                         escape=gs.is_escape_end, draw_offer=gs.draw_offer, last_result=gs.last_result,
                         clock_active=gs.clock_active, preparation_active=gs.preparation_active,
                         match_id=self.match.match_id, events=self.match.log[-80:],
                         enabled_cards=self.match.enabled_cards)
        return value

    def timing(self):
        gs = self.match.gs if self.match else None
        paused = bool(gs and gs.phase != "GAMEOVER" and self.connected < 2)
        settlement = (max(0, gs.result_timer - self.match.wall())
                      if gs and gs.phase == "RESULT" else None)
        return dict(revision=self.revision, paused=paused,
                    clock_active=gs.clock_active if gs else 0,
                    preparation_active=gs.preparation_active if gs else 0,
                    players=[dict(id=p, clock=self.match.remaining[p],
                                  preparation=self.match.preparation_remaining[p])
                             for p in (1, 2)] if gs else [],
                    reconnect_seconds=max(0, self.grace - (self.clock() - self.missing_since))
                    if paused and self.missing_since is not None else None,
                    settlement_seconds=settlement)

    async def deliver(self, pid, socket, *, force=False):
        seat = self.seats[pid]
        # Broadcasts and action responses share this lock and a single baseline.
        async with seat.send_lock:
            if seat.socket is not socket:
                return
            now = self.clock()
            changed = seat.view is None or seat.view["revision"] != self.revision
            gs = self.match.gs if self.match else None
            calibration = bool(gs and gs.phase != "GAMEOVER" and
                               (self.timer.get("enabled") or gs.phase == "RESULT"
                                or self.connected < 2) and now - seat.clock_sent >= 5)
            if not force and not changed and not calibration:
                return  # No projection, JSON serialization or network traffic while idle.
            # Engine effects contain mutable counters. Freeze exactly what goes
            # on the wire before yielding, so a later move cannot alter this baseline.
            view = copy.deepcopy(self.snapshot(pid)) if force or changed else None
            timing = self.timing()
            if view is not None:
                value = ({**view, "protocol": 2} if force or seat.view is None
                         else make_patch(seat.view, view))
                value["timing"] = timing
            else:
                value = {"type": "clock", **timing}
            await asyncio.wait_for(socket.send_json(value), 2)
            if seat.socket is socket:
                if view is not None:
                    seat.view = view
                seat.clock_sent = now

    async def broadcast(self):
        async def deliver(pid, seat, socket):
            try:
                await self.deliver(pid, socket)
            except (OSError, RuntimeError, WebSocketDisconnect, asyncio.TimeoutError):
                self.detach(pid, socket)
                try:
                    await socket.close(code=1011)
                except (OSError, RuntimeError):
                    pass
        await asyncio.gather(*(deliver(p, s, s.socket) for p, s in list(self.seats.items()) if s.socket))


class RoomService:
    def __init__(self, rules, timer, *, max_rooms=64, grace=60, lobby_ttl=300, idle_ttl=1800):
        self.rules, self.timer = rules, timer
        self.max_rooms, self.grace = max_rooms, grace
        self.lobby_ttl, self.idle_ttl = lobby_ttl, idle_ttl
        self.rooms = {}
        self.attempts = {}
        self.connections = 0

    def limit(self, peer):
        now = time.monotonic()
        self.attempts = {k: v for k, v in self.attempts.items() if now - v[-1] < 60}
        if peer not in self.attempts and len(self.attempts) >= 2048:
            raise RoomError("服务器繁忙，请稍后重试。")
        times = self.attempts.setdefault(peer, deque())
        while times and now - times[0] >= 60:
            times.popleft()
        if len(times) >= 40:
            raise RoomError("请求过于频繁，请一分钟后重试。")
        times.append(now)

    def create(self, name, *, rules=None, timer=None):
        if len(self.rooms) >= self.max_rooms:
            raise RoomError("房间已满，请稍后重试。")
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        while code in self.rooms:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        room = Room(code, name, self.rules if rules is None else rules,
                    self.timer if timer is None else timer, grace=self.grace)
        self.rooms[code] = room
        room.task = asyncio.create_task(self.run(room), name=f"room-{code}")
        return room

    def get(self, code):
        room = self.rooms.get(code)
        if room is None or room.closed:
            raise RoomError("房间不存在或已过期。")
        return room

    async def run(self, room):
        try:
            while not room.closed:
                now = room.clock()
                if (now - room.created > 7200 or now - room.updated > self.idle_ttl
                        or not room.match and now - room.created > self.lobby_ttl):
                    break
                room.advance()
                await room.broadcast()
                await asyncio.sleep(.25)
        finally:
            room.closed = True
            self.rooms.pop(room.code, None)
            for seat in room.seats.values():
                if seat.socket:
                    try:
                        await seat.send(seat.socket, {"type": "closed", "message": "房间已到期，请重新建房。"})
                        await seat.socket.close(code=1001)
                    except (OSError, RuntimeError, WebSocketDisconnect, asyncio.TimeoutError):
                        pass

    async def close(self):
        tasks = [r.task for r in self.rooms.values() if r.task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
