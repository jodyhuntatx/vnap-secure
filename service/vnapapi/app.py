"""HTTP API of the vnap-secure simulation service (FastAPI; OpenAPI description at /api/openapi.json).

Run behind a TLS reverse proxy (see README.md); the service account is the only one with
access to docker."""

import asyncio
import json
import os
import re
import threading
import time
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlsplit
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import backup as backups
from .config import load_config
from .control import ControlError, publish, read_retained, request as mqtt_request
from .db import Database
from .runs import RunError, RunManager
from .security import CSRF_HEADER, ROLES, SESSION_COOKIE, TOKEN_PREFIX, TOTP_REQUIRED, Accounts, check_password_policy

from vnapsim.check import compute_metrics, default_expectations, evaluate  # noqa: E402  (path set by .runs)
from vnapsim.common import EVENT_KINDS, ORIGIN, env_of  # noqa: E402
from vnapsim.events import collect_events, describe  # noqa: E402
from vnapsim.schema import SCENARIO_SCHEMA  # noqa: E402
from vnapsim.status import Simulation, build_status, observer_reports  # noqa: E402
from vnapsim.scenario import load_scenario  # noqa: E402
from vnapsim.scoring import score_run  # noqa: E402

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "ui")

STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(cfg=None, start_workers=True):
    cfg = cfg or load_config()
    db = Database(os.path.join(cfg["server"]["data_dir"], "vnapapi.db"))
    accounts = Accounts(db, cfg)
    runs = RunManager(db, cfg, start_workers=start_workers)
    app = FastAPI(title="vnap-secure simulation service", version="0.1", openapi_url="/api/openapi.json",
                  docs_url="/api/docs", redoc_url=None)
    app.state.db, app.state.accounts, app.state.runs, app.state.cfg = db, accounts, runs, cfg

    # ------------------------------------------------------------ helpers
    def address(request):
        return request.client.host if request.client else "?"

    def audit(user, action, request, target=None, detail=None):
        db.audit(user["username"] if user else None, action, target, detail, address(request))

    @app.exception_handler(RunError)
    async def run_error(request, exc):
        return JSONResponse({"error": str(exc), "errors": exc.errors}, status_code=exc.status)

    @app.exception_handler(ControlError)
    async def control_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=502)

    tiles = urlsplit(cfg["ui"]["tile_url"].replace("{s}", "*"))   # {s}: Leaflet's a/b/c subdomains
    tile_origin = f"{tiles.scheme}://{tiles.netloc}" if tiles.netloc else ""
    csp = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: " + tile_origin
           + "; connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = csp
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "no-store"
        return response

    def principal(request: Request):
        """The caller: an API token (Authorization: Bearer vnap_...) or a session cookie, whose
        state-changing requests must carry the session's CSRF token in X-CSRF-Token."""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            user = accounts.api_token(token) if token.startswith(TOKEN_PREFIX) else None
            if not user:
                raise HTTPException(401, "invalid API token")
            return user
        cookie = request.cookies.get(SESSION_COOKIE)
        user = accounts.session(cookie) if cookie else None
        if not user:
            raise HTTPException(401, "not logged in")
        if request.method in STATE_CHANGING and request.headers.get(CSRF_HEADER) != user["csrf"]:
            raise HTTPException(403, f"missing or wrong {CSRF_HEADER} header")
        return user

    def session_user(user=Depends(principal)):
        """Account security settings change only in an interactive session, never with an API token."""
        if "token_id" in user:
            raise HTTPException(403, "use a login session for this, not an API token")
        return user

    def admin(user=Depends(principal)):
        if user["role"] != "admin":
            raise HTTPException(403, "admins only")
        return user

    def public_user(u):
        return {k: u[k] for k in ("username", "role", "disabled", "created_at", "created_by") if k in u} | \
            {"locked": u.get("locked_until", 0) > time.time(), "totp": bool(u.get("totp_enabled"))}

    def run_for(user, run_id, write=False):
        run = runs.get(run_id)
        if not run:
            raise HTTPException(404, "no such run")
        owner_or_admin = user["role"] == "admin" or run["owner_id"] == user["id"]
        if not owner_or_admin and (write or user["username"] not in run["shared_with"]):
            raise HTTPException(404 if not write else 403, "no such run" if not write else "only the owner can do this")
        if write and user["role"] == "viewer":
            raise HTTPException(403, "viewers cannot change runs")
        return run

    def public_run(run):
        return {k: run[k] for k in ("id", "owner", "scenario", "overrides", "state", "instance", "vnap_run_id", "image",
                                    "seed", "error", "created_at", "started_at", "stopped_at", "stop_reason", "shared_with")} | \
            {"deadline": run["deadline"] if run["state"] in ("running", "stopping", "stopped") else None}

    def station_names(sim):
        return {Simulation.name(i): int(env_of(i)["VANETZA_STATION_ID"]) for i in sim.stations}

    # ------------------------------------------------------------ auth
    class Login(BaseModel):
        username: str = Field(max_length=64)
        password: str = Field(max_length=1024)
        totp_code: Optional[str] = Field(None, max_length=32)   # TOTP or recovery code, when the account has one

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response):
        if not accounts.limiter.allow(address(request)):
            db.audit(body.username, "login.rate_limited", None, None, address(request))
            raise HTTPException(429, "too many login attempts; wait a minute")
        if cfg["server"]["cookie_secure"] and request.url.scheme != "https" and request.url.hostname not in ("localhost", "127.0.0.1"):
            # the browser would drop the Secure session cookie and every later request would be 401
            raise HTTPException(400, "this service needs HTTPS: open it through the TLS proxy (api/deploy/Caddyfile), "
                                     "or set cookie_secure = false in config.toml for a test on a trusted network")
        user, reason = accounts.login(body.username, body.password, address(request), body.totp_code)
        if reason == TOTP_REQUIRED:
            # the password was right: the client asks for the second factor and sends both again
            return JSONResponse({"detail": reason, "totp_required": True}, status_code=401)
        if not user:
            db.audit(body.username, "login.failed", None, {"reason": reason}, address(request))
            raise HTTPException(401, reason)
        token, csrf = accounts.new_session(user, address(request))
        response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=cfg["server"]["cookie_secure"], samesite="strict",
                            max_age=3600 * cfg["server"]["session_hours"], path="/")
        audit(user, "login", request, None, {"second_factor": user["second_factor"]} if user["second_factor"] else None)
        if user["second_factor"] == "recovery":
            audit(user, "totp.recovery_code_used", request, user["username"])
        return {"username": user["username"], "role": user["role"], "csrf_token": csrf}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, user=Depends(principal)):
        if request.cookies.get(SESSION_COOKIE):
            accounts.end_session(request.cookies[SESSION_COOKIE])
        response.delete_cookie(SESSION_COOKIE, path="/")
        audit(user, "logout", request)
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user=Depends(principal)):
        return {"username": user["username"], "role": user["role"], "csrf_token": user.get("csrf")}

    class PasswordChange(BaseModel):
        current_password: str = Field(max_length=1024)
        new_password: str = Field(max_length=1024)

    @app.post("/api/auth/password")
    def change_password(body: PasswordChange, request: Request, user=Depends(principal)):
        if not accounts.login(user["username"], body.current_password, address(request), second_factor=False)[0]:
            raise HTTPException(403, "current password is wrong")
        try:
            accounts.set_password(user["username"], body.new_password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit(user, "password.changed", request, user["username"])
        return {"ok": True, "note": "all sessions ended; log in again"}

    # ------------------------------------------------------------ TOTP (optional second factor)
    class TotpSetup(BaseModel):
        password: str = Field(max_length=1024)

    class TotpCode(BaseModel):
        code: str = Field(max_length=32)

    class TotpConfirm(BaseModel):
        password: str = Field(max_length=1024)
        code: str = Field(max_length=32)

    def confirm_password(user, password, request):
        if not accounts.login(user["username"], password, address(request), second_factor=False)[0]:
            raise HTTPException(403, "password is wrong")

    def confirm_code(user, code):
        row = db.one("SELECT * FROM users WHERE id = ?", (user["id"],))
        if not accounts.check_second_factor(row, code):
            raise HTTPException(403, "TOTP or recovery code is wrong")

    @app.get("/api/auth/totp")
    def totp_status(user=Depends(principal)):
        return accounts.totp_status(db.one("SELECT * FROM users WHERE id = ?", (user["id"],)))

    @app.post("/api/auth/totp/setup")
    def totp_setup(body: TotpSetup, request: Request, user=Depends(session_user)):
        """A new secret for the authenticator app (QR code and text); active only after /enable."""
        confirm_password(user, body.password, request)
        return accounts.totp_setup(user)

    @app.post("/api/auth/totp/enable")
    def totp_enable(body: TotpCode, request: Request, user=Depends(session_user)):
        try:
            codes = accounts.totp_enable(user, body.code)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit(user, "totp.enabled", request, user["username"])
        return {"enabled": True, "recovery_codes": codes,
                "note": "each recovery code logs in once in place of a TOTP code; store them safely, they are not shown again"}

    @app.post("/api/auth/totp/recovery-codes")
    def totp_recovery_codes(body: TotpConfirm, request: Request, user=Depends(session_user)):
        """New recovery codes (the old ones stop working)."""
        if not user["totp_enabled"]:
            raise HTTPException(400, "TOTP is not enabled")
        confirm_password(user, body.password, request)
        confirm_code(user, body.code)
        audit(user, "totp.recovery_codes_renewed", request, user["username"])
        return {"recovery_codes": accounts.new_recovery_codes(user)}

    @app.post("/api/auth/totp/disable")
    def totp_disable(body: TotpConfirm, request: Request, user=Depends(session_user)):
        if not user["totp_enabled"]:
            raise HTTPException(400, "TOTP is not enabled")
        confirm_password(user, body.password, request)
        confirm_code(user, body.code)
        accounts.totp_disable(user["username"])
        audit(user, "totp.disabled", request, user["username"])
        return {"enabled": False}

    # ------------------------------------------------------------ users (admin)
    class NewUser(BaseModel):
        username: str = Field(max_length=64)
        password: str = Field(max_length=1024)
        role: str = "user"

    @app.get("/api/users")
    def list_users(user=Depends(admin)):
        return [public_user(u) for u in db.query("SELECT * FROM users ORDER BY username")]

    @app.post("/api/users", status_code=201)
    def create_user(body: NewUser, request: Request, user=Depends(admin)):
        try:
            accounts.create_user(body.username, body.password, body.role, created_by=user["username"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit(user, "user.created", request, body.username, {"role": body.role})
        return public_user(db.one("SELECT * FROM users WHERE username = ?", (body.username,)))

    class UserChange(BaseModel):
        role: Optional[str] = None
        disabled: Optional[bool] = None
        unlock: Optional[bool] = None
        reset_totp: Optional[bool] = None   # for a lost authenticator: the user logs in with the password alone

    @app.patch("/api/users/{username}")
    def change_user(username: str, body: UserChange, request: Request, user=Depends(admin)):
        target = db.one("SELECT * FROM users WHERE username = ?", (username,))
        if not target:
            raise HTTPException(404, "no such user")
        if body.role is not None:
            if body.role not in ROLES:
                raise HTTPException(400, f"role must be one of {', '.join(ROLES)}")
            db.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, target["id"]))
        if body.disabled is not None:
            if username == user["username"] and body.disabled:
                raise HTTPException(400, "you cannot disable yourself")
            db.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(body.disabled), target["id"]))
            if body.disabled:
                accounts.revoke_sessions(username)
        if body.unlock:
            db.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (target["id"],))
        if body.reset_totp:
            accounts.totp_disable(username)
            accounts.revoke_sessions(username)
        audit(user, "user.changed", request, username, body.model_dump(exclude_none=True))
        return public_user(db.one("SELECT * FROM users WHERE username = ?", (username,)))

    class PasswordReset(BaseModel):
        password: str = Field(max_length=1024)

    @app.post("/api/users/{username}/password")
    def reset_password(username: str, body: PasswordReset, request: Request, user=Depends(admin)):
        if not db.one("SELECT id FROM users WHERE username = ?", (username,)):
            raise HTTPException(404, "no such user")
        try:
            accounts.set_password(username, body.password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit(user, "user.password_reset", request, username)
        return {"ok": True}

    # ------------------------------------------------------------ API tokens
    class NewToken(BaseModel):
        name: str = Field(min_length=1, max_length=64)

    @app.get("/api/tokens")
    def list_tokens(user=Depends(principal)):
        return db.query("SELECT id, name, created_at, last_used, revoked FROM tokens WHERE user_id = ? ORDER BY id", (user["id"],))

    @app.post("/api/tokens", status_code=201)
    def create_token(body: NewToken, request: Request, user=Depends(principal)):
        tid, token = accounts.new_api_token(user, body.name)
        audit(user, "token.created", request, str(tid), {"name": body.name})
        return {"id": tid, "name": body.name, "token": token, "note": "shown once; use as 'Authorization: Bearer <token>'"}

    @app.delete("/api/tokens/{token_id}")
    def revoke_token(token_id: int, request: Request, user=Depends(principal)):
        if not db.one("SELECT id FROM tokens WHERE id = ? AND user_id = ?", (token_id, user["id"])):
            raise HTTPException(404, "no such token")
        db.execute("UPDATE tokens SET revoked = 1 WHERE id = ?", (token_id,))
        audit(user, "token.revoked", request, str(token_id))
        return {"ok": True}

    # ------------------------------------------------------------ scenarios
    @app.get("/api/scenarios")
    def scenarios(user=Depends(principal)):
        return runs.catalogue(user)

    @app.get("/api/scenarios/{kind}/{name}")
    def scenario_text(kind: str, name: str, user=Depends(principal)):
        if kind not in ("templates", "scenarios"):
            raise HTTPException(404, "kind is templates or scenarios")
        path = runs.scenario_path(user, **({"template": name} if kind == "templates" else {"scenario": name}))
        with open(path) as f:
            return {"name": name, "text": f.read()}

    @app.get("/api/schema")
    def schema(user=Depends(principal)):
        return SCENARIO_SCHEMA

    class ScenarioRef(BaseModel):
        scenario: Optional[str] = None
        template: Optional[str] = None
        scenario_text: Optional[str] = None
        overrides: List[str] = []

    @app.post("/api/scenarios/validate")
    def validate(body: ScenarioRef, user=Depends(principal)):
        path = runs.scenario_path(user, body.scenario, body.template, body.scenario_text)
        try:
            sc = runs.validate(user, path, body.overrides)
        except RunError as e:
            return {"valid": False, "errors": e.errors}
        finally:
            if body.scenario_text is not None:
                os.remove(path)
        return {"valid": True, "errors": [], "stations": [
            {"name": st["base_name"], "station_id": st["station_id"], "ip": st["ip"], "assigned": st["assigned"]}
            for st in sc["stations"]]}

    # ------------------------------------------------------------ runs
    class NewRun(ScenarioRef):
        duration_minutes: Optional[int] = None

    @app.get("/api/runs")
    def list_runs(user=Depends(principal)):
        return [public_run(r) for r in runs.visible(user)]

    @app.post("/api/runs", status_code=202)
    def create_run(body: NewRun, request: Request, user=Depends(principal)):
        run = runs.create(user, body.scenario, body.template, body.scenario_text, body.overrides, body.duration_minutes)
        audit(user, "run.created", request, run["id"], {"scenario": run["scenario"], "overrides": body.overrides})
        return public_run(run)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, user=Depends(principal)):
        run = run_for(user, run_id)
        out = public_run(run)
        if run["state"] == "running":
            out["status"] = build_status(runs.simulation(run))
        return out

    @app.post("/api/runs/{run_id}/stop", status_code=202)
    def stop_run(run_id: str, request: Request, user=Depends(principal)):
        run_for(user, run_id, write=True)
        runs.stop(run_id, f"stopped by {user['username']}")
        audit(user, "run.stopped", request, run_id)
        return public_run(runs.get(run_id))

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str, request: Request, user=Depends(principal)):
        run_for(user, run_id, write=True)
        runs.delete(run_id)
        audit(user, "run.deleted", request, run_id)
        return {"ok": True}

    class Share(BaseModel):
        username: str = Field(max_length=64)
        remove: bool = False

    @app.post("/api/runs/{run_id}/share")
    def share_run(run_id: str, body: Share, request: Request, user=Depends(principal)):
        run_for(user, run_id, write=True)
        other = db.one("SELECT id FROM users WHERE username = ?", (body.username,))
        if not other:
            raise HTTPException(404, "no such user")
        if body.remove:
            db.execute("DELETE FROM run_shares WHERE run_id = ? AND user_id = ?", (run_id, other["id"]))
        else:
            db.execute("INSERT OR IGNORE INTO run_shares (run_id, user_id) VALUES (?, ?)", (run_id, other["id"]))
        audit(user, "run.unshared" if body.remove else "run.shared", request, run_id, {"with": body.username})
        return public_run(runs.get(run_id))

    # ------------------------------------------------------------ live
    def parse_kinds(kinds):
        chosen = {k for k in (kinds or "").split(",") if k}
        if chosen - set(EVENT_KINDS):
            raise HTTPException(400, f"kinds: {', '.join(EVENT_KINDS)}")
        return chosen or None

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, since_s: float = 60, live_s: float = 0, kinds: Optional[str] = None,
               stations: Optional[str] = None, user=Depends(principal)):
        """Events from the logs of the last since_s seconds; with live_s > 0 also live MQTT
        messages (rx/tx/control) for that long (at most 30 s)."""
        sim = runs.simulation(run_for(user, run_id))
        if not (0 <= since_s <= 86400 and 0 <= live_s <= 30):
            raise HTTPException(400, "since_s 0..86400, live_s 0..30")
        chosen = parse_kinds(kinds)
        names = {s for s in (stations or "").split(",") if s} or None
        evs, _, _ = collect_events(sim, live_s, since=time.time() - since_s, kinds=chosen, stations=names)
        return [{**e, "text": describe(e)} for e in evs]

    @app.get("/api/runs/{run_id}/events/stream")
    async def event_stream(run_id: str, request: Request, kinds: Optional[str] = None, user=Depends(principal)):
        """Server-sent events: the run's log events as they happen (polled every 2 s, for at most 30 minutes)."""
        run = run_for(user, run_id)
        chosen = parse_kinds(kinds)

        async def gen():
            last = time.time() - 5
            end = time.time() + 1800
            seen = set()
            while time.time() < end and not await request.is_disconnected():
                current = runs.get(run_id)
                if current["state"] != "running":
                    yield f"event: end\ndata: {json.dumps({'state': current['state']})}\n\n"
                    return
                sim = await run_in_threadpool(runs.simulation, current)
                evs, _, _ = await run_in_threadpool(collect_events, sim, 0, last - 1, None, chosen, None)
                for e in evs:
                    key = (e["t"], e.get("station"), e.get("kind"), e.get("action"))
                    if key not in seen:
                        seen.add(key)
                        yield f"data: {json.dumps({**e, 'text': describe(e)})}\n\n"
                if evs:
                    last = max(e["t"] for e in evs)
                yield ": keep-alive\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(gen(), media_type="text/event-stream")

    position_cache = {}   # run id -> (time, positions): one probe container serves every viewer of a map
    position_locks = {}

    @app.get("/api/runs/{run_id}/positions")
    def positions(run_id: str, user=Depends(principal)):
        """Current position of every moving station (retained position updates on the control channel)."""
        run = run_for(user, run_id)
        with position_locks.setdefault(run_id, threading.Lock()):
            cached = position_cache.get(run_id)
            if cached and time.time() - cached[0] < 1.5:
                return cached[1]
            sim = runs.simulation(run)
            if not sim.broker:
                return {}
            ids = {v: k for k, v in station_names(sim).items()}
            found = read_retained(sim.ctl, Simulation.name(sim.broker), "vnap/position/+", sim.control_auth(), wait_s=1)
            out = {ids.get(int(t.rsplit("/", 1)[1]), t): p for t, p in found.items() if t.rsplit("/", 1)[1].isdigit()}
            position_cache[run_id] = (time.time(), out)
            return out

    @app.get("/api/runs/{run_id}/eavesdropper")
    def eavesdropper(run_id: str, user=Depends(principal)):
        sim = runs.simulation(run_for(user, run_id))
        return {"reports": observer_reports(sim), "score": score_run(sim)}

    class Control(BaseModel):
        action: str
        station: str = Field(max_length=64)
        index: Optional[int] = Field(None, ge=0, le=100000)
        duration: Optional[int] = Field(None, ge=0, le=255)
        lock_handle: Optional[int] = Field(None, ge=1)
        reason: Optional[str] = Field(None, max_length=64)

    @app.post("/api/runs/{run_id}/control")
    def control(run_id: str, body: Control, request: Request, user=Depends(principal)):
        """Pseudonym control: change (optionally to index), trigger (IDCHANGE-TRIGGER), lock / unlock (ID-LOCK)."""
        sim = runs.simulation(run_for(user, run_id, write=True))
        if body.action not in ("change", "trigger", "lock", "unlock"):
            raise HTTPException(400, "action: change, trigger, lock, unlock")
        sid = station_names(sim).get(body.station)
        if sid is None or not sim.broker:
            raise HTTPException(404, "no such station with a control channel in this run")
        payload = {"event_id": f"api-{int(time.time() * 1000)}", "action": body.action,
                   "reason": re.sub(r"[^\w .:-]", "", body.reason or f"api {user['username']}")}
        for key in ("index", "duration", "lock_handle"):
            if getattr(body, key) is not None:
                payload[key] = getattr(body, key)
        answer = mqtt_request(sim.ctl, Simulation.name(sim.broker), f"vnap/pseudonym/{sid}/change",
                              f"vnap/pseudonym/{sid}/status", payload, sim.control_auth())
        audit(user, "run.control", request, run_id, {"station": body.station, **payload,
                                                     "result": (answer or {}).get("result", "no answer")})
        return {"sent": payload, "answer": answer,
                "ok": bool(answer) and answer.get("result") in ("changed", "locked", "unlocked")}

    class Position(BaseModel):
        lat: float = Field(ge=-90, le=90)
        lon: float = Field(ge=-180, le=180)
        speed: Optional[float] = Field(None, ge=0, le=163.82)
        heading: Optional[float] = Field(None, ge=0, lt=360)

    @app.post("/api/runs/{run_id}/stations/{station}/position")
    def move(run_id: str, station: str, body: Position, request: Request, user=Depends(principal)):
        """Move a station (it must have a position channel: a mobility key in the scenario)."""
        sim = runs.simulation(run_for(user, run_id, write=True))
        sid = station_names(sim).get(station)
        info = next((i for i in sim.stations if Simulation.name(i) == station), None)
        if sid is None or not env_of(info).get("POSITION_CONTROL_BROKER"):
            raise HTTPException(404, "no such station with a position channel in this run")
        payload = body.model_dump(exclude_none=True)
        publish(sim.ctl, Simulation.name(sim.broker), f"vnap/position/{sid}", payload, sim.control_auth())
        audit(user, "run.move", request, run_id, {"station": station, **payload})
        return {"ok": True, "sent": payload}

    # ------------------------------------------------------------ results
    class Check(BaseModel):
        duration_s: float = Field(15, ge=1, le=60)
        expect: List[str] = []
        defaults: bool = True

    @app.post("/api/runs/{run_id}/check")
    def check(run_id: str, body: Check, request: Request, user=Depends(principal)):
        run = run_for(user, run_id, write=True)
        sim = runs.simulation(run)
        status = build_status(sim)
        evs, start, end = collect_events(sim, body.duration_s)
        metrics = compute_metrics(sim, status, evs, end - start)
        exps = (default_expectations(status) if body.defaults else []) + list(body.expect)
        results = []
        for e in exps:
            try:
                results.append(evaluate(e, metrics))
            except ValueError as err:
                raise HTTPException(400, f"expectation {e!r}: {err}")
        verdict = "pass" if all(r["ok"] for r in results) else "fail"
        out = {"verdict": verdict, "expectations": results, "metrics": metrics, "window_s": round(end - start, 1)}
        db.execute("INSERT INTO checks (run_id, created_at, verdict, result) VALUES (?, ?, ?, ?)",
                   (run_id, time.time(), verdict, json.dumps(out)))
        audit(user, "run.check", request, run_id, {"verdict": verdict})
        return out

    @app.get("/api/runs/{run_id}/checks")
    def checks(run_id: str, user=Depends(principal)):
        run_for(user, run_id)
        return [{**c, "result": json.loads(c["result"])} for c in
                db.query("SELECT id, created_at, verdict, result FROM checks WHERE run_id = ? ORDER BY id", (run_id,))]

    @app.get("/api/runs/{run_id}/results")
    def results(run_id: str, user=Depends(principal)):
        run_for(user, run_id)
        d = os.path.join(runs.results_dir, run_id)
        return [{"name": f, "bytes": os.path.getsize(os.path.join(d, f))} for f in runs.results(run_id)]

    @app.get("/api/runs/{run_id}/results/{name}")
    def result_file(run_id: str, name: str, user=Depends(principal)):
        run_for(user, run_id)
        if name not in runs.results(run_id):   # only listed files: no path traversal
            raise HTTPException(404, "no such result")
        return FileResponse(os.path.join(runs.results_dir, run_id, name), filename=f"{run_id}-{name}")

    # ------------------------------------------------------------ backups (admin)
    @app.get("/api/backups")
    def list_backups(user=Depends(admin)):
        """Archives in [backup] dir, newest first (download them on the server, not through the API)."""
        return [{k: a[k] for k in ("name", "bytes", "created_at")} for a in backups.archives(cfg["backup"]["dir"])]

    @app.post("/api/backups", status_code=201)
    def create_backup(request: Request, user=Depends(admin)):
        try:
            path = backups.create(cfg)
            manifest = backups.verify(path)
        except backups.BackupError as e:
            raise HTTPException(500, f"backup failed: {e}")
        name = os.path.basename(path)
        audit(user, "backup.created", request, name, {"files": len(manifest["files"]), "bytes": os.path.getsize(path)})
        return {"name": name, "bytes": os.path.getsize(path), "files": len(manifest["files"]), "counts": manifest["counts"]}

    # ------------------------------------------------------------ audit, health
    @app.get("/api/audit")
    def audit_log(limit: int = 200, username: Optional[str] = None, user=Depends(admin)):
        limit = max(1, min(limit, 5000))
        rows = db.query("SELECT * FROM audit WHERE (? IS NULL OR username = ?) ORDER BY id DESC LIMIT ?", (username, username, limit))
        return [{**r, "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]

    @app.get("/api/healthz")
    def health():
        return {"ok": True}

    # ------------------------------------------------------------ web UI
    @app.get("/api/ui-config")
    def ui_config():
        return {"tile_url": cfg["ui"]["tile_url"], "tile_attribution": cfg["ui"]["tile_attribution"], "origin": list(ORIGIN)}

    @app.get("/api/runs/{run_id}/layout")
    def layout(run_id: str, user=Depends(principal)):
        """The scenario's description, where the stations start and how they move, and the mix zones."""
        run = run_for(user, run_id)
        try:
            sc = load_scenario(run["scenario_file"], run["overrides"], run["instance"] or 0)
        except Exception as e:  # noqa: BLE001  (the run's file is validated; report rather than fail)
            raise HTTPException(409, f"cannot read the run's scenario: {e}")
        stations = []
        for st in sc["stations"]:
            mob = st.get("mobility") or {}
            start = mob.get("start") or [float(st["env"].get("VANETZA_LATITUDE", ORIGIN[0])),
                                         float(st["env"].get("VANETZA_LONGITUDE", ORIGIN[1]))]
            stations.append({"name": st["name"], "station_id": st["station_id"], "station_type": st["station_type"],
                             "start": start, "pseudonyms": bool(st.get("pseudonyms")),
                             "mobility": {k: mob[k] for k in ("route", "loop", "crossing", "arm_m", "speed_kmh") if k in mob}})
        zones = ((sc["control"] or {}).get("mobility") or {}).get("mix_zones", []) if sc["control"] else []
        return {"description": sc["description"], "stations": stations, "mix_zones": zones,
                "control": bool(sc["control"]), "pki": bool(sc["pki"]),
                "eavesdropper": bool(sc["eavesdropper"])}

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")

    if os.path.isdir(UI_DIR):
        app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

    return app


app = None if os.environ.get("VNAP_API_NO_APP") else create_app()
