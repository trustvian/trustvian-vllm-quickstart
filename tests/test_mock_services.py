"""The fake CRM's routes and its injected errors, and how the tools take those errors."""
import json
import random
import urllib.error
import urllib.request

import pytest

import mock_services
import tools


def request(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(tools.CRM + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def error_rate():
    """Sets the CRM's error rate (and a fixed seed) for one test."""
    def set_rate(rate: float, seed: int = 7):
        mock_services.ERROR_RATE = rate
        mock_services.RNG = random.Random(seed)
    yield set_rate
    mock_services.ERROR_RATE = 0.0


def test_existing_routes_are_unchanged():
    assert request("GET", "/customers/c-1001") == (200, mock_services.CUSTOMERS["c-1001"])
    assert request("GET", "/customers/c-9999")[0] == 404
    assert request("POST", "/replies", {"customer_id": "c-1001", "message": "hi"}) == (201, {"status": "sent"})
    assert request("POST", "/exports", {"customer_id": "c-1001"}) == (201, {"status": "exported"})


def test_invoices():
    status, body = request("GET", "/invoices/c-1001")
    assert status == 200 and [i["id"] for i in body["invoices"]] == ["inv-3101", "inv-3102"]
    assert request("GET", "/invoices/c-1002") == (200, {"customer_id": "c-1002", "invoices": []})
    assert request("GET", "/invoices/c-9999")[0] == 404


def test_refunds_and_plans():
    assert request("POST", "/refunds", {"customer_id": "c-1001", "invoice_id": "inv-3102"}) == (
        201, {"status": "refunded"})
    assert request("POST", "/plans", {"customer_id": "c-1002", "plan": "pro"}) == (201, {"status": "changed"})
    assert request("POST", "/plans", {"customer_id": "c-1002", "plan": "gold"})[0] == 400


def test_error_rate_one_fails_every_call_429_for_refunds(error_rate):
    error_rate(1.0)
    assert request("GET", "/customers/c-1001")[0] == 503
    assert request("GET", "/invoices/c-1001")[0] == 503
    assert request("POST", "/replies", {"customer_id": "c-1001", "message": "x"})[0] == 503
    assert request("POST", "/plans", {"customer_id": "c-1001", "plan": "pro"})[0] == 503
    assert request("POST", "/refunds", {"customer_id": "c-1001", "invoice_id": "inv-3101"})[0] == 429


def test_error_rate_is_the_share_of_failed_requests(error_rate):
    error_rate(0.05, seed=11)
    same = random.Random(11)
    expected = sum(same.random() < 0.05 for _ in range(400))
    failed = sum(request("GET", "/customers/c-1001")[0] == 503 for _ in range(400))
    assert failed == expected
    assert 8 <= failed <= 35


def test_error_rate_zero_never_fails(error_rate):
    error_rate(0.0)
    assert all(request("GET", "/customers/c-1002")[0] == 200 for _ in range(100))


def test_the_tools_turn_errors_into_a_short_failure(error_rate):
    error_rate(1.0)
    tools.CALLS.clear()
    assert tools.lookup_customer(customer_id="c-1001") == (
        "lookup_customer failed: the CRM answered 503; try again later")
    assert tools.issue_refund(customer_id="c-1001", invoice_id="inv-3101") == (
        "issue_refund failed: the CRM answered 429; try again later")
    assert tools.CALLS == [("lookup_customer", False), ("issue_refund", False)]


def test_the_tools_survive_an_unreachable_crm(monkeypatch):
    monkeypatch.setattr(tools, "CRM", "http://127.0.0.1:9")
    assert tools.change_plan(customer_id="c-1001", plan="pro") == (
        "change_plan failed: the CRM is unreachable; try again later")


def test_the_reference_tools_answer_as_before():
    tools.CALLS.clear()
    assert json.loads(tools.lookup_customer(customer_id="c-1001"))["name"] == "Alice Carter"
    assert tools.lookup_customer(customer_id="c-9999") == "customer c-9999 not found"
    assert tools.send_reply(customer_id="c-1001", message="hi") == "reply sent"
    assert tools.export_customer(customer_id="c-1001") == "export exported"
    assert tools.CALLS == [("lookup_customer", True), ("lookup_customer", True),
                           ("send_reply", True), ("export_customer", True)]
