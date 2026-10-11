"""live.py, the supervisor, with a stub worker instead of the agent: no model, no Trustvian."""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = [sys.executable, str(ROOT / "tests" / "stub_worker.py")]
TICKET = re.compile(r"^(\S+-agent)\s+ticket (\d+)\s+tools: (.*?)  \(")


def live(tmp_path, timeout=30, **env):
    """Runs live.py to completion; returns (exit status, stdout)."""
    environment = {**os.environ, "STUB_DIR": str(tmp_path), "LIVE_INTERVAL_SECONDS": "0", **env}
    done = subprocess.run(
        [sys.executable, str(ROOT / "live.py"), "--", *STUB],
        env=environment, capture_output=True, text=True, timeout=timeout,
    )
    return done.returncode, done.stdout


def tickets(stdout: str) -> list:
    """(actor, ticket number, [tools]) per ticket line."""
    found = []
    for line in stdout.splitlines():
        match = TICKET.match(line)
        if match:
            found.append((match[1], int(match[2]), match[3].split(", ")))
    return found


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it is gone for every purpose here.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return state.stdout.strip() not in ("", "Z") and not state.stdout.strip().startswith("Z")


def test_one_worker_per_role_with_its_own_service_name(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="1")
    assert status == 0, out
    for role in ("support", "billing", "accounts"):
        assert (tmp_path / f"{role}.service").read_text().split() == [f"{role}-agent"]
    assert sorted(actor for actor, _, _ in tickets(out)) == [
        "accounts-agent", "billing-agent", "support-agent"]


def test_live_agents_selects_the_roles(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="1", LIVE_AGENTS="billing")
    assert status == 0, out
    assert [actor for actor, _, _ in tickets(out)] == ["billing-agent"]
    assert not (tmp_path / "support.service").exists()


def test_max_tickets_is_per_worker_and_ends_the_run(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="3")
    assert status == 0, out
    for actor in ("support-agent", "billing-agent", "accounts-agent"):
        assert [n for a, n, _ in tickets(out) if a == actor] == [1, 2, 3]
    assert "support-agent 3 tickets" in out


def test_the_role_binds_its_own_tools(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="1", LIVE_DRIFT_EVERY="0")
    by_actor = {actor: tools for actor, _, tools in tickets(out)}
    assert by_actor == {
        "support-agent": ["lookup_customer", "send_reply"],
        "billing-agent": ["lookup_customer", "lookup_invoice", "issue_refund"],
        "accounts-agent": ["lookup_customer", "change_plan"],
    }


def test_drift_every_nth_billing_ticket_only(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="6", LIVE_DRIFT_EVERY="3")
    assert status == 0, out
    drifted = [(a, n) for a, n, tools in tickets(out) if "export_customer" in tools]
    assert drifted == [("billing-agent", 3), ("billing-agent", 6)]
    assert out.count("[drift]") == 2


def test_drift_off(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="4", LIVE_DRIFT_EVERY="0", LIVE_AGENTS="billing")
    assert status == 0, out
    assert all("export_customer" not in tools for _, _, tools in tickets(out))


def test_a_crashed_worker_is_restarted_and_carries_on(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="4", STUB_CRASH_AT="2")
    assert status == 0, out
    assert "live: billing-agent exited with status 1; restarting in 2 s" in out
    assert "live: restarting billing-agent (restart 1)" in out
    # The ticket that crashed is worked again by the new process; the total holds.
    for actor in ("support-agent", "billing-agent", "accounts-agent"):
        assert [n for a, n, _ in tickets(out) if a == actor] == [1, 2, 3, 4]
    assert len((tmp_path / "billing.pid").read_text().split()) == 2


def start_forever(tmp_path, **env):
    environment = {**os.environ, "STUB_DIR": str(tmp_path), "LIVE_INTERVAL_SECONDS": "0.2", **env}
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "live.py"), "--", *STUB],
        env=environment, stdout=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:  # every worker has handled a ticket
        if all((tmp_path / f"{r}.pid").exists() for r in ("support", "billing", "accounts")):
            break
        time.sleep(0.1)
    time.sleep(1)
    pids = [int(p) for r in ("support", "billing", "accounts")
            for p in (tmp_path / f"{r}.pid").read_text().split()]
    return proc, pids


def test_sigterm_stops_every_worker_and_leaves_nothing_behind(tmp_path):
    proc, pids = start_forever(tmp_path, STUB_GRANDCHILD="1")
    assert len(pids) == 6 and all(alive(p) for p in pids)
    started = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0, out
    assert time.monotonic() - started < 3
    assert "live: stopping" in out
    time.sleep(0.2)
    assert [p for p in pids if alive(p)] == []


def test_a_worker_that_ignores_sigterm_is_killed_after_the_grace_period(tmp_path):
    proc, pids = start_forever(tmp_path, STUB_IGNORE_TERM="1")
    started = time.monotonic()
    proc.send_signal(signal.SIGINT)
    out, _ = proc.communicate(timeout=15)
    took = time.monotonic() - started
    assert proc.returncode == 0, out
    assert 4.5 < took < 8
    time.sleep(0.2)
    assert [p for p in pids if alive(p)] == []


def test_ticket_lines_carry_no_ticket_text(tmp_path):
    import roles

    status, out = live(tmp_path, LIVE_MAX_TICKETS="8")
    texts = [text for spec in roles.ROLES.values() for _, text in spec["tickets"]]
    customers = {cid for spec in roles.ROLES.values() for cid, _ in spec["tickets"]}
    assert status == 0
    for planted in texts + sorted(customers):
        assert planted not in out
