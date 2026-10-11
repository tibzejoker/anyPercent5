import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from anyp5.engine import Engine
from anyp5.server import App, handler_for
from tests.test_engine import DIFF


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.app = App(Engine(config={"canary_every": 1e9}), "admin-secret")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()

    def call(self, path, body=None, token=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_review_round_trip_is_blind(self):
        tokens = {n: self.call("/admin/join", {"login": n}, "admin-secret")[1]["token"] for n in ("alice", "bob", "carol")}
        self.assertEqual(self.call("/admin/join", {"login": "x"}, "wrong")[0], 403)
        self.call("/admin/submit", {"pr": 7, "head": "abc", "author": "alice", "diff": DIFF}, "admin-secret")
        self.app.engine.tick(0)
        reviewer = self.app.engine.q("select reviewer from assignments")[0]["reviewer"]
        code, data = self.call("/reviews", token=tokens[reviewer])
        self.assertEqual(code, 200)
        job = data["reviews"][0]
        self.assertEqual(set(job), {"assignment", "diff"})
        self.assertNotIn("alice", json.dumps(job))
        self.assertEqual(self.call("/verdict", {"assignment": job["assignment"], "verdict": "approve"}, tokens[reviewer])[0], 200)
        self.assertEqual(self.call("/status")[1]["changes"], {"approved": 1})
        self.assertEqual(self.call("/reviews", token="nope")[0], 401)


if __name__ == "__main__":
    unittest.main()
