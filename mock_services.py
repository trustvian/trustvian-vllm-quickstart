"""A fake CRM on 127.0.0.1:8081, standard library only.

GET  /customers/<id>      -> customer record
POST /replies             -> records a reply
POST /exports             -> records an export (the candidate's new behavior)

Run it in its own terminal:  python mock_services.py

CRM_BIND / CRM_PORT change the listen address (Compose sets CRM_BIND=0.0.0.0
so the agent container can reach it; on a laptop the loopback default is right).
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

BIND = os.environ.get("CRM_BIND", "127.0.0.1")
PORT = int(os.environ.get("CRM_PORT", "8081"))

CUSTOMERS = {
    "c-1001": {"id": "c-1001", "name": "Alice Carter", "plan": "pro", "open_tickets": 1},
    "c-1002": {"id": "c-1002", "name": "Ben Walker", "plan": "free", "open_tickets": 0},
}
STATE = {"replies": [], "exports": []}


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path.startswith("/customers/"):
            customer = CUSTOMERS.get(self.path.rsplit("/", 1)[-1])
            if customer:
                return self._json(200, customer)
            return self._json(404, {"error": "no such customer"})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/replies":
            STATE["replies"].append(self._body())
            return self._json(201, {"status": "sent"})
        if self.path == "/exports":
            STATE["exports"].append(self._body())
            return self._json(201, {"status": "exported"})
        return self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):  # keep the terminal quiet
        print("mock:", fmt % args)


if __name__ == "__main__":
    print(f"listening on http://{BIND}:{PORT}", flush=True)
    HTTPServer((BIND, PORT), Handler).serve_forever()
