#!/usr/bin/env bash
# Shared helpers for pipeline scripts.

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
BLUE="\033[34m"
RESET="\033[0m"

pids=()
interrupt_count=0
cleanup_in_progress="false"

check_uv() {
    if ! command -v uv >/dev/null 2>&1; then
        echo -e "${RED}Error: 'uv' is required but not found.${RESET}"
        exit 1
    fi
}

load_env() {
    if [ -f "$ROOT_DIR/.env" ]; then
        set -a
        # shellcheck source=/dev/null
        source "$ROOT_DIR/.env"
        set +a
    fi
}

run_service() {
    local name="$1"
    shift
    echo -e "[${BLUE}${name}${RESET}] $*"
    "$@" &
    pids+=($!)
}

stop_services() {
    if [ "${#pids[@]}" -eq 0 ]; then
        return
    fi
    for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            wait "$pid" 2>/dev/null || true
        fi
    done
}

force_stop_services() {
    if [ "${#pids[@]}" -eq 0 ]; then
        return
    fi
    for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}

cleanup() {
    if [[ "$cleanup_in_progress" == "true" ]]; then
        return
    fi
    cleanup_in_progress="true"
    if [ "${#pids[@]}" -eq 0 ]; then
        return
    fi
    echo -e "${YELLOW}Stopping services...${RESET}"
    stop_services
}

handle_interrupt() {
    interrupt_count=$((interrupt_count + 1))
    cleanup_in_progress="true"
    if [[ "$interrupt_count" -ge 2 ]]; then
        echo -e "${RED}Force killing services...${RESET}"
        force_stop_services
        exit 130
    fi
    if [ "${#pids[@]}" -eq 0 ]; then
        exit 130
    fi
    echo -e "${YELLOW}Stopping services... (press Ctrl+C again to force)${RESET}"
    stop_services
}
