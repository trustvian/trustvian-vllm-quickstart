"""The agent's tools and their JSON schemas. Imports neither OpenTelemetry nor Trustvian.

Each function lives in REGISTRY under the exact name the model sees in its
schema. The harness (run.py) swaps REGISTRY entries for traced wrappers in
place, so this file and agent.py stay free of tracing code. Trustvian shows
the name as the behavior (`tool · lookup_customer`).

A tool never raises: an error from the CRM (live mode's occasional 503 or
429) comes back to the model as a short failure string.
"""
import json
import os

import requests

CRM = os.environ.get("CRM_URL", "http://127.0.0.1:8081")


# Every call as (tool name, succeeded), for live mode's line per ticket.
CALLS = []


def _crm(method: str, path: str, **kwargs):
    """(status, body) from the CRM; status 0 when it could not be reached."""
    try:
        response = requests.request(method, f"{CRM}{path}", timeout=10, **kwargs)
    except requests.RequestException:
        return 0, {}
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {}


def _result(name: str, ok: bool, text: str) -> str:
    CALLS.append((name, ok))
    return text


def _failed(name: str, status: int) -> str:
    reason = "the CRM is unreachable" if status == 0 else f"the CRM answered {status}"
    return _result(name, False, f"{name} failed: {reason}; try again later")


def lookup_customer(customer_id: str) -> str:
    status, body = _crm("GET", f"/customers/{customer_id}")
    if status == 200:
        return _result("lookup_customer", True, json.dumps(body))
    if status == 404:
        return _result("lookup_customer", True, f"customer {customer_id} not found")
    return _failed("lookup_customer", status)


def send_reply(customer_id: str, message: str) -> str:
    status, body = _crm("POST", "/replies", json={"customer_id": customer_id, "message": message})
    if status in (200, 201):
        return _result("send_reply", True, f"reply {body.get('status', 'unknown')}")
    return _failed("send_reply", status)


# The candidate's new capability. In real life this is a pull request that adds
# a tool; here an environment variable stands in for the code change so the
# reference and candidate runs come from the same checkout.
def export_customer(customer_id: str) -> str:
    status, body = _crm("POST", "/exports", json={"customer_id": customer_id})
    if status in (200, 201):
        return _result("export_customer", True, f"export {body.get('status', 'unknown')}")
    return _failed("export_customer", status)


# Live mode's billing and accounts tools.
def lookup_invoice(customer_id: str) -> str:
    status, body = _crm("GET", f"/invoices/{customer_id}")
    if status == 200:
        return _result("lookup_invoice", True, json.dumps(body["invoices"]))
    if status == 404:
        return _result("lookup_invoice", True, f"customer {customer_id} not found")
    return _failed("lookup_invoice", status)


def issue_refund(customer_id: str, invoice_id: str) -> str:
    status, body = _crm("POST", "/refunds", json={"customer_id": customer_id, "invoice_id": invoice_id})
    if status in (200, 201):
        return _result("issue_refund", True, f"refund {body.get('status', 'unknown')}")
    return _failed("issue_refund", status)


def change_plan(customer_id: str, plan: str) -> str:
    status, body = _crm("POST", "/plans", json={"customer_id": customer_id, "plan": plan})
    if status in (200, 201):
        return _result("change_plan", True, f"plan {body.get('status', 'unknown')}")
    if status == 400:
        return _result("change_plan", True, "plan must be one of free, pro, team")
    return _failed("change_plan", status)


REGISTRY = {
    "lookup_customer": lookup_customer,
    "send_reply": send_reply,
    "export_customer": export_customer,
    "lookup_invoice": lookup_invoice,
    "issue_refund": issue_refund,
    "change_plan": change_plan,
}


def _schema(name: str, description: str, properties: dict, required: list) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


SCHEMAS = {
    "lookup_customer": _schema(
        "lookup_customer",
        "Look up a customer record by id. Returns name, plan and open ticket count.",
        {"customer_id": {"type": "string"}},
        ["customer_id"],
    ),
    "send_reply": _schema(
        "send_reply",
        "Send a short reply to the customer. Use it exactly once per ticket.",
        {"customer_id": {"type": "string"}, "message": {"type": "string"}},
        ["customer_id", "message"],
    ),
    "export_customer": _schema(
        "export_customer",
        "Export the full customer record to the partner data warehouse.",
        {"customer_id": {"type": "string"}},
        ["customer_id"],
    ),
    "lookup_invoice": _schema(
        "lookup_invoice",
        "List a customer's invoices: id, month, amount and status.",
        {"customer_id": {"type": "string"}},
        ["customer_id"],
    ),
    "issue_refund": _schema(
        "issue_refund",
        "Refund one invoice of a customer.",
        {"customer_id": {"type": "string"}, "invoice_id": {"type": "string"}},
        ["customer_id", "invoice_id"],
    ),
    "change_plan": _schema(
        "change_plan",
        "Change a customer's subscription plan to free, pro or team.",
        {"customer_id": {"type": "string"}, "plan": {"type": "string", "enum": ["free", "pro", "team"]}},
        ["customer_id", "plan"],
    ),
}

BASE_TOOLS = ["lookup_customer", "send_reply"]
CANDIDATE_TOOLS = BASE_TOOLS + ["export_customer"]


def tools_for(variant: str) -> list:
    """The tool schemas offered to the model for this variant."""
    names = CANDIDATE_TOOLS if variant == "candidate" else BASE_TOOLS
    return [SCHEMAS[name] for name in names]


def tools_named(names: list) -> list:
    """Live mode: the schemas a role's ticket offers, by name (roles.py)."""
    return [SCHEMAS[name] for name in names]
