#!/bin/sh
set -eu

PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export PATH

PROJECT_DIR=${STOCK_STRATEGY_PROJECT_DIR:-"$HOME/stock-strategy-api"}
ENV_FILE=${STOCK_STRATEGY_ENV_FILE:-"$HOME/.config/stock-strategy-api.env"}
DOCKER_BIN=${STOCK_STRATEGY_DOCKER_BIN:-/usr/local/bin/docker}
DOCKER_IMAGE=${STOCK_STRATEGY_DOCKER_IMAGE:-stock-strategy-api:20260817-incremental}
LOCK_DIR="$PROJECT_DIR/.daily.lock"

acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" >"$LOCK_DIR/pid"
        return
    fi

    old_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        printf 'daily strategy job is already running with PID %s\n' "$old_pid" >&2
        exit 0
    fi

    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR"
    mkdir "$LOCK_DIR"
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

release_lock() {
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

acquire_lock
trap release_lock EXIT HUP INT TERM

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/logs"
cd "$PROJECT_DIR"
printf '{"event":"daily_job_start","time":"%s","project_dir":"%s","data_dir":"%s"}\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$PROJECT_DIR" "$PROJECT_DIR/data"

if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
    /usr/bin/open -gja Docker
    ready=0
    for _attempt in $(seq 1 24); do
        if "$DOCKER_BIN" info >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 5
    done
    if [ "$ready" -ne 1 ]; then
        printf 'Docker daemon did not become ready within 120 seconds\n' >&2
        exit 1
    fi
fi

"$DOCKER_BIN" run --rm --init \
    --name stock-strategy-api-daily \
    --env-file "$ENV_FILE" \
    -e STOCK_STRATEGY_DATA_DIR=/app/data \
    -e STOCK_STRATEGY_DATABASE_PATH=/app/data/strategy.sqlite3 \
    -v "$PROJECT_DIR/data:/app/data" \
    "$DOCKER_IMAGE" \
    python scripts/push_wecom.py "$@"
