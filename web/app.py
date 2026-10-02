"""Run with python -m uvicorn web.app:app --host 127.0.0.1 --port 8000."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import time

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from cards import CARDS, english_name
from web.config import ROOT, customization_options, load_clock, load_rules, settings_checked
from web.rooms import RoomError, RoomService

HEARTBEAT_TIMEOUT = 45


def create_app(*, config_path=None, timer_path=None, service_options=None, base_path=None):
    rules = load_rules(config_path or os.environ.get("NOIR_CONFIG", ROOT / "config.json"))
    timer = load_clock(timer_path or os.environ.get("NOIR_TIMER", ROOT / "timer.json"), rules)
    service = RoomService(rules, timer, **(service_options or {}))
    options = customization_options(rules, timer)
    public_origin = os.environ.get("NOIR_ORIGIN", "").rstrip("/")
    base_path = (os.environ.get("NOIR_BASE_PATH", "") if base_path is None else base_path).rstrip("/")
    if base_path and not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", base_path):
        raise ValueError("NOIR_BASE_PATH 必须是 /re7 这样的路径，或留空使用网站根目录。")

    @asynccontextmanager
    async def lifespan(app):
        yield
        await service.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.rooms = service
    app.state.base_path = base_path

    def valid_origin(connection):
        origin = connection.headers.get("origin")
        scheme = "https" if connection.url.scheme in ("https", "wss") else "http"
        expected = public_origin or f"{scheme}://{connection.headers.get('host', '')}"
        return origin == expected

    @app.middleware("http")
    async def security(request, call_next):
        if request.method == "POST" and request.headers.get("origin") and not valid_origin(request):
            response = JSONResponse({"error": "不允许跨站请求。"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "connect-src 'self'; img-src 'self' data:; "
                                       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        })
        return response

    async def payload(request, limit=2048):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise RoomError("请使用 JSON 请求。")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                raise RoomError("请求太大。")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise RoomError("JSON 格式错误。") from None
        if not isinstance(value, dict):
            raise RoomError("请求必须是对象。")
        return value

    def name_checked(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 20:
            raise RoomError("昵称需为 1–20 个字符。")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise RoomError("昵称包含无效字符。")
        return value.strip()

    def seat_response(room, pid):
        return dict(room=room.code, pid=pid, token=room.seats[pid].token)

    @app.exception_handler(RoomError)
    async def room_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get(base_path + "/healthz")
    async def health():
        return {"status": "ok"}

    if base_path:
        # Internal container health probes keep working regardless of the public path.
        app.add_api_route("/healthz", health, methods=["GET"])

        async def directory_root(request: Request):
            suffix = "?" + request.url.query if request.url.query else ""
            # Relative redirect remains HTTPS behind a TLS-terminating proxy.
            return RedirectResponse(base_path + "/" + suffix, status_code=307)

        app.add_api_route(base_path, directory_root, methods=["GET", "HEAD"])

    @app.get(base_path + "/api/catalog")
    async def catalog():
        return {name: dict(name=name, title=row[0], category=row[1], description=row[2],
                           english=english_name(name)) for name, row in CARDS.items()}

    @app.get(base_path + "/api/settings")
    async def settings():
        return options

    def checked(data):
        try:
            return settings_checked(data, rules, timer)
        except (ValueError, OverflowError) as exc:
            raise RoomError(str(exc)[:180]) from None

    @app.post(base_path + "/api/settings/validate")
    async def validate_settings(request: Request):
        service.limit(request.client.host if request.client else "unknown")
        data = await payload(request, 16384)
        selected_rules, selected_timer = checked(data)
        return dict(config=selected_rules, timer=selected_timer)

    @app.post(base_path + "/api/rooms", status_code=201)
    async def create_room(request: Request):
        service.limit(request.client.host if request.client else "unknown")
        data = await payload(request, 16384)
        selected_rules, selected_timer = checked(data)
        room = service.create(name_checked(data.get("name")), rules=selected_rules, timer=selected_timer)
        return seat_response(room, 1)

    @app.post(base_path + "/api/rooms/{code}/settings")
    async def update_settings(code: str, request: Request):
        service.limit(request.client.host if request.client else "unknown")
        data = await payload(request, 16384)
        room = service.get(code.upper())
        pid = room.identify(data.get("token"))
        selected_rules, selected_timer = checked(data)
        room.configure(pid, data.get("revision"), selected_rules, selected_timer)
        await room.broadcast()
        return {"revision": room.revision}

    @app.post(base_path + "/api/rooms/{code}/join")
    async def join_room(code: str, request: Request):
        service.limit(request.client.host if request.client else "unknown")
        data = await payload(request)
        name = name_checked(data.get("name"))
        room = service.get(code.upper())
        return seat_response(room, room.join(name))

    @app.websocket(base_path + "/ws/{code}")
    async def connect(socket: WebSocket, code: str):
        if not valid_origin(socket):
            await socket.close(code=1008)
            return
        room = None
        pid = None
        attached = False
        counted = False
        try:
            service.limit(socket.client.host if socket.client else "unknown")
            if service.connections >= service.max_rooms * 3:
                raise RoomError("连接数已满。")
            service.connections += 1
            counted = True
            await socket.accept()
            raw = await asyncio.wait_for(socket.receive_text(), 5)
            if len(raw.encode("utf-8")) > 2048:
                raise RoomError("请求太大。")
            auth = json.loads(raw)
            if not isinstance(auth, dict) or auth.get("type") != "auth":
                raise RoomError("请先认证房间席位。")
            room = service.get(code.upper())
            pid = room.identify(auth.get("token"))
            room.attach(pid, socket)
            attached = True
            seat = room.seats[pid]
            await room.broadcast()
            start, count = time.monotonic(), 0
            while not room.closed:
                raw = await asyncio.wait_for(socket.receive_text(), HEARTBEAT_TIMEOUT)
                if len(raw.encode("utf-8")) > 2048:
                    raise RoomError("请求太大。")
                now = time.monotonic()
                if now - start >= 1:
                    start, count = now, 0
                count += 1
                if count > 20:
                    raise RoomError("操作过于频繁。")
                try:
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise RoomError("操作必须是对象。")
                    if data.get("type") == "ping":
                        await seat.send(socket, {"type": "pong"})
                        continue
                    if data.get("type") == "sync":
                        room.advance()
                        await room.deliver(pid, socket, force=True)
                        continue
                    room.command(pid, data)
                except (RoomError, json.JSONDecodeError) as exc:
                    await seat.send(socket, {"type": "error", "message": str(exc)[:180]})
                await room.broadcast()
        except asyncio.TimeoutError:
            try:
                if not attached:
                    await socket.send_json({"type": "fatal", "message": "席位认证超时。"})
                # An authenticated heartbeat timeout is recoverable. Do not send
                # 'fatal': the browser must retain its seat for the grace period.
                await socket.close(code=1012 if attached else 1008)
            except (RuntimeError, OSError, WebSocketDisconnect):
                pass
        except (RoomError, ValueError, KeyError) as exc:
            try:
                await socket.send_json({"type": "fatal", "message": str(exc)[:180] or "连接超时。"})
            except (RuntimeError, OSError, WebSocketDisconnect):
                pass
            try:
                await socket.close(code=1008)
            except (RuntimeError, OSError, WebSocketDisconnect):
                pass
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            if room and pid and attached:
                room.detach(pid, socket)
            if counted:
                service.connections -= 1

    app.mount(base_path or "/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
    return app


app = create_app()
