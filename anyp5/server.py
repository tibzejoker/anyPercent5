"""Blind review dispatcher: a small JSON-over-HTTP server around the engine.

Why a server and not only GitHub: calibration canaries only work if a reviewer cannot tell them from real
changes. On a public forge a planted PR is visible to everyone, so reviews are served from here, blind
(no title, no author), and only verdicts flow back to the forge.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import Engine
from .tiers import parse_diff


class App:
    def __init__(self, engine, admin_token, forge=None, sync_every=300, tick_every=60):
        self.engine, self.admin, self.forge = engine, admin_token, forge
        self.lock = threading.Lock()
        self.sync_every, self.tick_every = sync_every, tick_every

    def loop(self):
        last_sync = 0
        while True:
            now = time.time()
            with self.lock:
                self.engine.tick(now)
                if self.forge and now - last_sync >= self.sync_every:
                    self.forge.sync(self.engine, now)
                    last_sync = now
            time.sleep(self.tick_every)

    def handle(self, method, path, token, body):
        now = time.time()
        e = self.engine
        if path.startswith("/admin/"):
            if not self.admin or token != self.admin:
                return 403, {"error": "admin token required"}
            if path == "/admin/join":
                return 200, {"token": e.join(body["login"], now)}
            if path == "/admin/task":
                return 200, {"task": e.add_task(body["title"], body.get("tier", "green"), now)}
            if path == "/admin/submit":
                paths, added = parse_diff(body["diff"])
                return 200, {"change": e.submit(body["pr"], body["head"], body["author"], paths, added, body["diff"], now)}
            if path == "/admin/merged":
                e.merged(body["change"], now)
                return 200, {}
            if path == "/admin/reverted":
                e.reverted(body["change"], now)
                return 200, {}
            return 404, {"error": "unknown admin route"}
        member = e.member(token=token) if token else None
        if path == "/status":
            counts = {r["state"]: r["n"] for r in e.q("select state, count(*) n from changes where kind='pr' group by state")}
            return 200, {"changes": counts, "releasable": e.releasable(now),
                         "open_tasks": e.one("select count(*) n from tasks where state='open'")["n"]}
        if member is None or member["banned"]:
            return 401, {"error": "unknown or banned member"}
        login = member["login"]
        if path == "/alive":
            e.alive(login, now)
            return 200, {}
        if path == "/reviews":
            return 200, {"reviews": e.next_review(login, now)}
        if path == "/verdict":
            e.verdict(login, body["assignment"], body["verdict"], now, body.get("severity", "none"), body.get("reason", ""))
            return 200, {}
        if path == "/tasks":
            rows = e.q("select id, title, tier, branch from tasks where state='open' order by id limit 50")
            return 200, {"tasks": [dict(r) for r in rows]}
        if path == "/claim":
            e.claim(login, body["task"], now)
            return 200, {}
        if path == "/handover":
            e.handover(body["task"], body["branch"], now)
            return 200, {}
        if path == "/direct":
            e.direct(login, int(body["task"]), body["decision"], now, body.get("reason", ""))
            return 200, {}
        if path == "/me":
            me = {"login": login, "calibration": [member["cal_hits"], member["cal_seen"]],
                  "pending_reviews": len(e.pending_checks(login))}
            if login in e.cfg["architects"]:
                me["direction"] = e.awaiting_direction()
            return 200, me
        return 404, {"error": "unknown route"}


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            auth = self.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else None
            try:
                with app.lock:
                    code, payload = app.handle(method, self.path, token, body)
            except (ValueError, KeyError) as err:
                code, payload = 400, {"error": str(err)}
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.respond("GET")

        def do_POST(self):
            self.respond("POST")

        def log_message(self, *args):
            pass

    return Handler


def serve(db, host, port, forge=None, config=None):
    engine = Engine(db, config=config)
    app = App(engine, os.environ.get("ANYP5_ADMIN_TOKEN"), forge)
    threading.Thread(target=app.loop, daemon=True).start()
    ThreadingHTTPServer((host, port), handler_for(app)).serve_forever()
