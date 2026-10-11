"""Live mode's agent, per role, under run.py's instrumentation with a scripted model.

A TOOL span per tool call, an LLM span per model call, export_customer only on
a drift ticket, and no ticket text, reply text or tool argument in any span.
"""
import random

import pytest

import agent
import mock_services
import roles
from fake_openai import answer, tool_call

SPAN_KIND = "openinference.span.kind"
CUSTOMER = "c-1003"
TICKET = "SENTINEL-LIVE-TICKET-81f3: the invoice for March is missing"
REPLY = "SENTINEL-LIVE-REPLY-2be7: here is how to fix it"
INVOICE = "SENTINEL-LIVE-INVOICE-90aa"
PLAN = "SENTINEL-LIVE-PLAN-77c1"
ANSWER = "SENTINEL-LIVE-ANSWER-d41e: done"
PLANTED = (TICKET, REPLY, INVOICE, PLAN, ANSWER)

# What the scripted model asks for after lookup_customer, per role.
ROLE_CALLS = {
    "support": [("send_reply", {"customer_id": CUSTOMER, "message": REPLY})],
    "billing": [("lookup_invoice", {"customer_id": CUSTOMER}),
                ("issue_refund", {"customer_id": CUSTOMER, "invoice_id": INVOICE})],
    "accounts": [("change_plan", {"customer_id": CUSTOMER, "plan": PLAN})],
}


def of_kind(spans, kind: str) -> list:
    return [s for s in spans if s.attributes.get(SPAN_KIND) == kind]


def tool_names(spans) -> list:
    return [s.attributes.get("tool.name") for s in of_kind(spans, "TOOL")]


def run_ticket(monkeypatch, model, role: str, drift: bool = False) -> dict:
    monkeypatch.setitem(roles.ROLES[role], "tickets", [(CUSTOMER, TICKET)])
    ticket = roles.ticket(role, 20 if drift else 1, random.Random(0), drift_every=20)
    steps = [tool_call("lookup_customer", customer_id=CUSTOMER)]
    steps += [tool_call(name, **args) for name, args in ROLE_CALLS[role]]
    if drift:
        steps.append(tool_call("export_customer", customer_id=CUSTOMER))
    steps.append(answer(ANSWER))
    model.play(steps)
    agent.live_ticket(ticket)
    return ticket


@pytest.mark.parametrize("role", ["support", "billing", "accounts"])
def test_each_role_emits_its_tools_and_model_calls(spans, model, monkeypatch, role):
    ticket = run_ticket(monkeypatch, model, role)
    finished = spans()
    assert tool_names(finished) == ["lookup_customer"] + [name for name, _ in ROLE_CALLS[role]]
    assert len(of_kind(finished, "LLM")) == len(model.requests)
    # The model was offered exactly the role's tools.
    offered = [t["function"]["name"] for t in model.requests[0]["tools"]]
    assert sorted(offered) == sorted(ticket["tools"])


def test_a_drift_ticket_adds_export_customer_to_billing(spans, model, monkeypatch):
    run_ticket(monkeypatch, model, "billing", drift=True)
    assert tool_names(spans())[-1] == "export_customer"
    assert "export_customer" in [t["function"]["name"] for t in model.requests[0]["tools"]]


def test_a_failed_crm_call_is_a_tool_span_not_a_crash(spans, model, monkeypatch):
    monkeypatch.setattr(mock_services, "ERROR_RATE", 1.0)
    run_ticket(monkeypatch, model, "billing")
    finished = spans()
    assert tool_names(finished) == ["lookup_customer", "lookup_invoice", "issue_refund"]
    statuses = [s.attributes.get("http.response.status_code") or s.attributes.get("http.status_code")
                for s in finished if s.attributes.get("http.method") or s.attributes.get("http.request.method")]
    assert sorted(set(statuses)) == [429, 503]


@pytest.mark.parametrize("role", ["support", "billing", "accounts"])
def test_no_content_reaches_a_span_in_live_mode(spans, model, monkeypatch, role):
    run_ticket(monkeypatch, model, role, drift=role == "billing")
    finished = spans()
    assert finished
    for span in finished:
        for key, value in span.attributes.items():
            assert not key.startswith(("llm.input_messages", "llm.output_messages", "llm.prompts")), key
            if key in ("input.value", "output.value"):
                assert value == "__REDACTED__", (span.name, key)
            assert key != "tool.parameters", (span.name, key)
            text = str(value)
            for planted in PLANTED:
                assert planted not in text, (span.name, key)
            # The customer id reaches only the URL of the GET the tool itself
            # makes (/customers/<id>, /invoices/<id>).
            if CUSTOMER in text:
                assert key in ("http.url", "url.full"), (span.name, key)
        assert all(p not in span.name for p in PLANTED + (CUSTOMER,))
        for event in span.events:
            for value in event.attributes.values():
                assert all(p not in str(value) for p in PLANTED)
