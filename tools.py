"""The agent's tools and their JSON schemas. Imports neither OpenTelemetry nor Trustvian.

Each function lives in REGISTRY under the exact name the model sees in its
schema. The harness (run.py) swaps REGISTRY entries for traced wrappers in
place, so this file and agent.py stay free of tracing code. Trustvian shows
the name as the behavior (`tool · lookup_customer`).
"""
import json
import os

import requests

CRM = os.environ.get("CRM_URL", "http://127.0.0.1:8081")


def lookup_customer(customer_id: str) -> str:
    response = requests.get(f"{CRM}/customers/{customer_id}", timeout=10)
    if response.status_code != 200:
        return f"customer {customer_id} not found"
    return json.dumps(response.json())


def send_reply(customer_id: str, message: str) -> str:
    response = requests.post(
        f"{CRM}/replies", json={"customer_id": customer_id, "message": message}, timeout=10
    )
    return f"reply {response.json().get('status', 'unknown')}"


# The candidate's new capability. In real life this is a pull request that adds
# a tool; here an environment variable stands in for the code change so the
# reference and candidate runs come from the same checkout.
def export_customer(customer_id: str) -> str:
    response = requests.post(f"{CRM}/exports", json={"customer_id": customer_id}, timeout=10)
    return f"export {response.json().get('status', 'unknown')}"


REGISTRY = {
    "lookup_customer": lookup_customer,
    "send_reply": send_reply,
    "export_customer": export_customer,
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
}

BASE_TOOLS = ["lookup_customer", "send_reply"]
CANDIDATE_TOOLS = BASE_TOOLS + ["export_customer"]


def tools_for(variant: str) -> list:
    """The tool schemas offered to the model for this variant."""
    names = CANDIDATE_TOOLS if variant == "candidate" else BASE_TOOLS
    return [SCHEMAS[name] for name in names]
