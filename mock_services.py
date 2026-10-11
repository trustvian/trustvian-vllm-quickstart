"""A fake CRM on 127.0.0.1:8081, standard library only.

GET  /customers/<id>      -> customer record
POST /replies             -> records a reply
POST /exports             -> records an export (the candidate's new behavior)
GET  /invoices/<id>       -> the customer's invoices          (live mode)
POST /refunds             -> records a refund                 (live mode)
POST /plans               -> records a plan change            (live mode)

Run it in its own terminal:  python mock_services.py

CRM_BIND / CRM_PORT change the listen address (Compose sets CRM_BIND=0.0.0.0
so the agent container can reach it; on a laptop the loopback default is right).

CRM_ERROR_RATE (default 0.05) is the share of requests answered with an error
instead: 429 for /refunds, 503 for everything else, so live mode shows the
occasional failed call. 0 turns it off. CRM_ERROR_SEED makes the sequence
repeatable.
"""
import json
import os
import random
from http.server import BaseHTTPRequestHandler, HTTPServer

BIND = os.environ.get("CRM_BIND", "127.0.0.1")
PORT = int(os.environ.get("CRM_PORT", "8081"))
ERROR_RATE = float(os.environ.get("CRM_ERROR_RATE", "0.05"))
RNG = random.Random(os.environ.get("CRM_ERROR_SEED") or None)

CUSTOMERS = {
    "c-1001": {"id": "c-1001", "name": "Alice Carter", "plan": "pro", "open_tickets": 1},
    "c-1002": {"id": "c-1002", "name": "Ben Walker", "plan": "free", "open_tickets": 0},
    "c-1003": {"id": "c-1003", "name": "Chloe Diaz", "plan": "team", "open_tickets": 2},
    "c-1004": {"id": "c-1004", "name": "Dev Patel", "plan": "free", "open_tickets": 1},
    "c-1005": {"id": "c-1005", "name": "Emma Novak", "plan": "pro", "open_tickets": 0},
}
INVOICES = {
    "c-1001": [{"id": "inv-3101", "month": "2026-09", "amount": "49.00", "status": "paid"},
               {"id": "inv-3102", "month": "2026-09", "amount": "49.00", "status": "paid"}],
    "c-1002": [],
    "c-1003": [{"id": "inv-3301", "month": "2026-03", "amount": "120.00", "status": "paid"}],
    "c-1004": [],
    "c-1005": [{"id": "inv-3501", "month": "2026-09", "amount": "49.00", "status": "overdue"}],
}
PLANS = ("free", "pro", "team")
STATE = {"replies": [], "exports": [], "refunds": [], "plans": []}


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

    def _failed(self):
        """Answers with an error, CRM_ERROR_RATE of the time. True when it did."""
        if ERROR_RATE <= 0 or RNG.random() >= ERROR_RATE:
            return False
        if self.path == "/refunds":
            self._json(429, {"error": "too many refunds, try again later"})
        else:
            self._json(503, {"error": "temporarily unavailable"})
        return True

    def do_GET(self):
        if self._failed():
            return
        if self.path.startswith("/customers/"):
            customer = CUSTOMERS.get(self.path.rsplit("/", 1)[-1])
            if customer:
                return self._json(200, customer)
            return self._json(404, {"error": "no such customer"})
        if self.path.startswith("/invoices/"):
            customer_id = self.path.rsplit("/", 1)[-1]
            if customer_id in INVOICES:
                return self._json(200, {"customer_id": customer_id, "invoices": INVOICES[customer_id]})
            return self._json(404, {"error": "no such customer"})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path in ("/replies", "/exports", "/refunds", "/plans") and self._failed():
            return
        if self.path == "/replies":
            STATE["replies"].append(self._body())
            return self._json(201, {"status": "sent"})
        if self.path == "/exports":
            STATE["exports"].append(self._body())
            return self._json(201, {"status": "exported"})
        if self.path == "/refunds":
            STATE["refunds"].append(self._body())
            return self._json(201, {"status": "refunded"})
        if self.path == "/plans":
            body = self._body()
            if body.get("plan") not in PLANS:
                return self._json(400, {"error": f"plan must be one of {', '.join(PLANS)}"})
            STATE["plans"].append(body)
            return self._json(201, {"status": "changed"})
        return self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):  # keep the terminal quiet
        print("mock:", fmt % args)


if __name__ == "__main__":
    print(f"listening on http://{BIND}:{PORT}", flush=True)
    HTTPServer((BIND, PORT), Handler).serve_forever()
