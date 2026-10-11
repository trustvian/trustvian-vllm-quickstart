#!/usr/bin/env bash
#
# Entrypoint of the `agent` service. One subcommand per quick-start step:
#
#   reference            run the reference agent under `trustvian dev`
#   candidate            run the candidate agent (AGENT_VARIANT=candidate)
#   compare [REF CAND]   gate the last two runs (or the two ids given)
#   live                 live mode: live.py's agents, until stopped
#   trustvian ARGS...    any trustvian CLI command, --api-url already set
#   shell                a bash shell in the container
#
# The run ids are chosen here (ref-<utc timestamp>, cand-<utc timestamp>)
# and written to $STATE_DIR before dev starts, so `compare` with no
# arguments gates the most recent pair. dev would otherwise generate one,
# and reading it back out of the banner is more fragile than naming it.
set -euo pipefail

API_URL="${TRUSTVIAN_API_URL:-http://control-plane:8080}"
PROJECT="${TRUSTVIAN_PROJECT:-trustvian-vllm-quickstart}"
AGENT="${TRUSTVIAN_AGENT:-support-agent}"
STATE_DIR="${HOME}/.trustvian/compose"
WEB_HINT="${TRUSTVIAN_WEB_URL:-http://127.0.0.1:8080/}"
mkdir -p "$STATE_DIR"

usage() {
    sed -n '3,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

wait_for_control_plane() {
    local i
    for i in $(seq 1 60); do
        if curl -fsS -o /dev/null --max-time 2 "$API_URL/v1/projects?limit=1"; then
            return 0
        fi
        sleep 1
    done
    echo "agent: the control plane did not answer at $API_URL within 60s" >&2
    exit 3
}

# wait_for_model: vLLM loads weights for a minute or more after its container
# starts; a run that begins before /v1/models answers would fail on its first
# chat completion and dev would record a failed run for a reason that is not
# the agent's.
wait_for_model() {
    local base="${VLLM_BASE_URL:-http://vllm:8000/v1}" i
    for i in $(seq 1 "${VLLM_READY_SECONDS:-300}"); do
        if curl -fsS -o /dev/null --max-time 2 "$base/models"; then
            return 0
        fi
        [ "$i" -eq 1 ] && echo "agent: waiting for the model server at $base …"
        sleep 1
    done
    echo "agent: no model server answered at $base/models within ${VLLM_READY_SECONDS:-300}s" >&2
    exit 3
}

# run SIDE COMMAND... — one evaluation run through `trustvian dev`.
#
# --project, --agent and --candidate are explicit: there is no git checkout
# in the container for dev to derive them from, and the two sides must be
# different candidates or they would share one learning profile.
run() {
    local side="$1"; shift
    local run_id="${side}-$(date -u +%Y%m%dT%H%M%SZ)"
    clear_stale_baseline_locks
    printf '%s\n' "$run_id" >"$STATE_DIR/last-$side"
    echo "agent: run id $run_id (remembered as the last '$side' run)"
    echo "agent: live view on the host: $WEB_HINT"
    exec trustvian dev \
        --api-url "$API_URL" \
        --project "$PROJECT" \
        --agent "$AGENT" \
        --candidate "$side" \
        --run-id "$run_id" \
        --instrumentation existing \
        -- "$@"
}

# clear_stale_baseline_locks: dev guards each learning profile's baseline
# with a pid lock and releases it on every ordinary exit, Ctrl-C included. A
# container killed outright leaves the file behind in the shared state
# volume, and the pid in it is meaningless here — it may even be reused by
# this container's own dev. A fresh container has no other dev process, so
# any lock found is stale; cleared on the assumption the quick start makes
# anyway: one run at a time per side.
clear_stale_baseline_locks() {
    if [ -d "$HOME/.trustvian/dev" ] && ! pgrep -x trustvian >/dev/null 2>&1; then
        find "$HOME/.trustvian/dev" -name 'baseline-*.json.lock' -delete 2>/dev/null || true
    fi
}

last() {
    local file="$STATE_DIR/last-$1"
    [ -s "$file" ] || { echo "agent: no '$1' run recorded yet; run '$2' first" >&2; exit 2; }
    cat "$file"
}

case "${1:-help}" in
    reference)
        shift
        wait_for_control_plane
        wait_for_model
        export AGENT_VARIANT=reference
        run ref python run.py "$@"
        ;;
    candidate)
        shift
        wait_for_control_plane
        wait_for_model
        export AGENT_VARIANT=candidate
        run cand python run.py "$@"
        ;;
    live)
        shift
        wait_for_control_plane
        wait_for_model
        # One run for as long as the service is up; docker compose stop/down
        # sends dev SIGTERM, which ends live.py and completes the run.
        run live python live.py "$@"
        ;;
    compare)
        ref="${2:-$(last ref reference)}"
        cand="${3:-$(last cand candidate)}"
        wait_for_control_plane
        echo "agent: comparing $ref (reference) with $cand (candidate)"
        exec trustvian eval compare \
            --api-url "$API_URL" \
            --reference-run "$ref" --candidate-run "$cand" \
            --max-added-behaviors "${MAX_ADDED_BEHAVIORS:-0}" \
            --max-block-decisions "${MAX_BLOCK_DECISIONS:-0}" \
            --max-critical-risk-observations "${MAX_CRITICAL_RISK_OBSERVATIONS:-0}"
        ;;
    trustvian)
        shift
        # --api-url goes AFTER the subcommand: the CLI reads the first token
        # as the command name.
        exec trustvian "$@" --api-url "$API_URL"
        ;;
    shell)
        exec bash
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        # Anything else is run as-is, e.g. `python agent.py` for an untraced run.
        exec "$@"
        ;;
esac
