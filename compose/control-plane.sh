#!/bin/sh
#
# Runs trustvian-local for the Compose stack.
#
# trustvian-local refuses any --listen address that is not loopback: the
# runtime is unauthenticated and "do not expose it" is its own usage text.
# Inside a container, loopback is reachable by nothing else — not the agent
# service, not the browser on the host. So this script runs the runtime on
# 127.0.0.1:18080 and a socat relay on 0.0.0.0:8080 in front of it. The
# relay is reachable on the Compose network as control-plane:8080, and
# compose.yaml publishes it on the HOST's 127.0.0.1 only. The security
# property the runtime insists on (loopback only, on the machine you are
# sitting at) is therefore preserved at the host boundary.
set -eu

STATE_DIR="${TRUSTVIAN_STATE_DIR:-/var/lib/trustvian}"
INNER="127.0.0.1:18080"
PUBLIC_PORT="${TRUSTVIAN_PUBLIC_PORT:-8080}"

mkdir -p "$STATE_DIR"

trustvian-local --state-dir "$STATE_DIR" --listen "$INNER" &
LOCAL_PID=$!

socat "TCP-LISTEN:${PUBLIC_PORT},fork,reuseaddr" "TCP:${INNER}" &
SOCAT_PID=$!

stop() {
    kill -TERM "$SOCAT_PID" 2>/dev/null || true
    kill -TERM "$LOCAL_PID" 2>/dev/null || true
    wait "$LOCAL_PID" 2>/dev/null || true
    exit 0
}
trap stop TERM INT

echo "control-plane: trustvian-local on ${INNER}, relayed on 0.0.0.0:${PUBLIC_PORT}"
echo "control-plane: state in ${STATE_DIR}"

# If the runtime exits on its own (a corrupt database, a refused state
# directory), this script exits with its status and the container stops:
# the containerized form of failing closed.
status=0
wait "$LOCAL_PID" || status=$?
kill -TERM "$SOCAT_PID" 2>/dev/null || true
exit "$status"
