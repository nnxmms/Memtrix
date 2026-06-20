#!/bin/sh
# Supervisor entrypoint for the Memtrix agent.
#
# It runs the agent as a child process and restarts it when the web control
# panel requests a restart by creating the restart-request sentinel on the
# shared data volume. A crash-loop backoff prevents a broken config from
# hot-looping. No Docker socket is involved: only the agent PROCESS is cycled.

set -eu

DATA_DIR="${MEMTRIX_DATA_DIR:-/home/memtrix/data}"
RESTART_SENTINEL="${DATA_DIR}/.restart-request"
POLL_INTERVAL="${MEMTRIX_RESTART_POLL_SECONDS:-2}"

# Crash-loop backoff bounds (seconds)
MIN_BACKOFF=1
MAX_BACKOFF=30
backoff="${MIN_BACKOFF}"

child_pid=""

# Forward SIGTERM/SIGINT to the child and exit so `docker stop` is graceful.
term_handler() {
    if [ -n "${child_pid}" ] && kill -0 "${child_pid}" 2>/dev/null; then
        kill -TERM "${child_pid}" 2>/dev/null || true
        wait "${child_pid}" 2>/dev/null || true
    fi
    exit 0
}
trap term_handler TERM INT

# Clear any stale restart request from a previous run.
rm -f "${RESTART_SENTINEL}" 2>/dev/null || true

while true; do
    echo "[supervisor] starting agent: python -m src.app.main"
    start_ts="$(date +%s)"

    python -m src.app.main &
    child_pid="$!"

    # Watch for a restart request while the child runs.
    restart_requested=0
    while kill -0 "${child_pid}" 2>/dev/null; do
        if [ -f "${RESTART_SENTINEL}" ]; then
            echo "[supervisor] restart requested; stopping agent (pid ${child_pid})"
            rm -f "${RESTART_SENTINEL}" 2>/dev/null || true
            kill -TERM "${child_pid}" 2>/dev/null || true
            restart_requested=1
            break
        fi
        sleep "${POLL_INTERVAL}"
    done

    # Reap the child and capture its exit code.
    wait "${child_pid}" 2>/dev/null || true
    exit_code="$?"
    child_pid=""

    if [ "${restart_requested}" -eq 1 ]; then
        echo "[supervisor] agent stopped for restart; relaunching"
        backoff="${MIN_BACKOFF}"
        continue
    fi

    # Apply crash-loop backoff: reset it when the process ran for a while.
    end_ts="$(date +%s)"
    ran_for="$((end_ts - start_ts))"
    if [ "${ran_for}" -ge 60 ]; then
        backoff="${MIN_BACKOFF}"
    fi

    echo "[supervisor] agent exited (code ${exit_code}); restarting in ${backoff}s"
    sleep "${backoff}"
    backoff="$((backoff * 2))"
    if [ "${backoff}" -gt "${MAX_BACKOFF}" ]; then
        backoff="${MAX_BACKOFF}"
    fi
done
