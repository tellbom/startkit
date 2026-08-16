#!/bin/sh
set -eu

PROJECT_DIR=${STOCK_STRATEGY_PROJECT_DIR:-"$HOME/stock-strategy-api"}
ENV_FILE=${STOCK_STRATEGY_ENV_FILE:-"$HOME/.config/stock-strategy-api.env"}
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

set -a
. "$ENV_FILE"
set +a

export STOCK_STRATEGY_DATA_DIR="$PROJECT_DIR/data"
export STOCK_STRATEGY_DATABASE_PATH="$PROJECT_DIR/data/strategy.sqlite3"

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/logs"
cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" scripts/push_wecom.py "$@"
