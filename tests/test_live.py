"""live.py, the supervisor, with stubs instead of `trustvian dev` and the agent: no model, no Trustvian."""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = [sys.executable, str(ROOT / "tests" / "stub_worker.py")]
STUB_TRUSTVIAN = ROOT / "tests" / "stub_trustvian"
TICKET = re.compile(r"^(\S+-agent)\s+ticket (\d+)\s+tools: (.*?)  \(")
RUN = re.compile(r"^(\S+-agent)\s+run (\S+)$")
ROLES = ("support", "billing", "accounts")
API_URL = "http://127.0.0.1:65530"


def environment(tmp_path, **env) -> dict:
    return {**os.environ, "PATH": f"{STUB_TRUSTVIAN}{os.pathsep}{os.environ['PATH']}",
            "TRUSTVIAN_API_URL": API_URL, "STUB_DIR": str(tmp_path),
            "LIVE_INTERVAL_SECONDS": "0", **env}


def live(tmp_path, timeout=30, **env):
    """Runs live.py to completion; returns (exit status, stdout)."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "live.py"), "--", *STUB],
        env=environment(tmp_path, **env), capture_output=True, text=True, timeout=timeout,
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


def dev_args(tmp_path, actor: str) -> list:
    """The arguments of each `trustvian dev` started for actor, in order."""
    return [json.loads(line) for line in (tmp_path / f"{actor}.dev").read_text().splitlines()]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it is gone for every purpose here.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return state.stdout.strip() not in ("", "Z") and not state.stdout.strip().startswith("Z")


def test_one_dev_per_role_each_its_own_run(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="1")
    assert status == 0, out
    for role in ROLES:
        actor = f"{role}-agent"
        assert dev_args(tmp_path, actor) == [[
            "--api-url", API_URL, "--project", "trustvian-vllm-quickstart",
            "--agent", actor, "--candidate", f"live-{role}", "--instrumentation", "existing"]]
        # LIVE_ROLE picked the role; dev's --agent became OTEL_SERVICE_NAME.
        assert (tmp_path / f"{role}.service").read_text().split() == [actor]
    runs = dict(RUN.match(line).groups() for line in out.splitlines() if RUN.match(line))
    assert sorted(runs) == ["accounts-agent", "billing-agent", "support-agent"]
    assert all(run.startswith(f"dev-live-{actor.split('-')[0]}-") for actor, run in runs.items())
    for actor, run in runs.items():
        assert f"{actor:<15} run {run} complete" in out
    assert sorted(actor for actor, _, _ in tickets(out)) == [
        "accounts-agent", "billing-agent", "support-agent"]


def test_the_project_comes_from_trustvian_project(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="1", LIVE_AGENTS="support", TRUSTVIAN_PROJECT="other")
    assert status == 0, out
    args = dev_args(tmp_path, "support-agent")[0]
    assert args[args.index("--project") + 1] == "other"


def test_without_trustvian_api_url_it_refuses_and_names_the_control_plane_command(tmp_path):
    env = environment(tmp_path)
    del env["TRUSTVIAN_API_URL"]
    done = subprocess.run([sys.executable, str(ROOT / "live.py"), "--", *STUB],
                          env=env, capture_output=True, text=True, timeout=10)
    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr.count("\n") == 1
    assert "TRUSTVIAN_API_URL" in done.stderr and "trustvian-local --state-dir ~/.trustvian/live --listen 127.0.0.1:8080" in done.stderr
    assert list(tmp_path.iterdir()) == []  # no dev was started


def test_live_agents_selects_the_roles(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="1", LIVE_AGENTS="billing")
    assert status == 0, out
    assert [actor for actor, _, _ in tickets(out)] == ["billing-agent"]
    assert not (tmp_path / "support-agent.dev").exists()


def test_max_tickets_is_per_agent_and_ends_every_run(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="3")
    assert status == 0, out
    for actor in ("support-agent", "billing-agent", "accounts-agent"):
        assert [n for a, n, _ in tickets(out) if a == actor] == [1, 2, 3]
    assert "support-agent 3 tickets" in out
    assert out.count(" complete\n") == 3
    assert "restarting" not in out


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


def test_a_crashed_agent_is_restarted_as_a_new_run_and_carries_on(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="4", STUB_CRASH_AT="2")
    assert status == 0, out
    assert "live: billing-agent's dev exited with status 1; restarting in 2 s" in out
    assert "live: restarting billing-agent (restart 1)" in out
    # The ticket that crashed is worked again by the new run; the total holds.
    for actor in ("support-agent", "billing-agent", "accounts-agent"):
        assert [n for a, n, _ in tickets(out) if a == actor] == [1, 2, 3, 4]
    assert len(dev_args(tmp_path, "billing-agent")) == 2
    billing_runs = [m[2] for m in map(RUN.match, out.splitlines()) if m and m[1] == "billing-agent"]
    assert len(billing_runs) == 2 and billing_runs[0] != billing_runs[1]


def test_a_dev_that_cannot_start_is_retried(tmp_path):
    status, out = live(tmp_path, LIVE_MAX_TICKETS="1", LIVE_AGENTS="support", STUB_DEV_FAIL_ONCE="1")
    assert status == 0, out
    assert "support-agent   dev: creating the project: already_exists" in out
    assert "live: support-agent's dev exited with status 3; restarting in 2 s" in out
    assert [n for _, n, _ in tickets(out)] == [1]


def start_forever(tmp_path, **env):
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "live.py"), "--", *STUB],
        env=environment(tmp_path, LIVE_INTERVAL_SECONDS="0.2", **env), stdout=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:  # every agent has started
        if all((tmp_path / f"{r}.pid").exists() for r in ROLES):
            break
        time.sleep(0.1)
    time.sleep(1)
    pids = [int(p) for r in ROLES for p in (tmp_path / f"{r}.pid").read_text().split()]
    pids += [int(p) for r in ROLES for p in (tmp_path / f"{r}-agent.devpid").read_text().split()]
    return proc, pids


def test_sigterm_completes_every_run_and_leaves_nothing_behind(tmp_path):
    proc, pids = start_forever(tmp_path, STUB_GRANDCHILD="1")
    assert len(pids) == 9 and all(alive(p) for p in pids)  # 3 devs, 3 agents, 3 grandchildren
    started = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=15)
    assert proc.returncode == 0, out
    assert time.monotonic() - started < 3
    assert "live: stopping; each dev completes its run" in out
    assert out.count(" complete\n") == 3
    time.sleep(0.2)
    assert [p for p in pids if alive(p)] == []


def test_a_dev_that_ignores_sigint_is_killed_with_its_agent_after_the_grace_period(tmp_path):
    proc, pids = start_forever(tmp_path, STUB_DEV_IGNORE_INT="1", STUB_GRANDCHILD="1",
                               LIVE_STOP_GRACE_SECONDS="2")
    started = time.monotonic()
    proc.send_signal(signal.SIGINT)
    out, _ = proc.communicate(timeout=15)
    took = time.monotonic() - started
    assert proc.returncode == 0, out
    assert 1.5 < took < 6
    assert "did not finish within 2 s; killing it" in out
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
