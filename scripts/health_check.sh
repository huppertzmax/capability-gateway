#!/usr/bin/env bash
# Probe pipeline services for liveness/readiness via their health endpoints.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<EOF
Usage: $0 --arch <simple|simple-crew|gateway|gateway-rag> [--mcp-bloat] [--quiet]
Checks MCP and agent readiness endpoints for the selected architecture.
EOF
    exit 1
}

ARCH=""
QUIET="false"
CLI_MCP_BLOAT="false"

while [[ $# -gt 0 ]]; do
    case $1 in
        --arch)
            ARCH="$2"; shift 2;;
        --mcp-bloat)
            CLI_MCP_BLOAT="true"; shift 1;;
        --quiet)
            QUIET="true"; shift 1;;
        *)
            usage;;
    esac
done

if [[ -z "$ARCH" ]]; then
    usage
fi

load_env

ENABLE_MCP_BLOAT="false"
if [[ "$CLI_MCP_BLOAT" == "true" ]]; then
    ENABLE_MCP_BLOAT="true"
fi

endpoints=()
HEALTH_CHECK_ATTEMPTS="${HEALTH_CHECK_ATTEMPTS:-30}"
HEALTH_CHECK_DELAY_SECONDS="${HEALTH_CHECK_DELAY_SECONDS:-1}"

add_endpoint() {
    endpoints+=("$1")
}

probe_endpoint() {
    local url="$1"
    local attempts="$2"
    local delay_seconds="$3"
    local attempt
    for ((attempt=1; attempt<=attempts; attempt++)); do
        if curl -fsS --max-time 5 "$url" >/dev/null; then
            return 0
        fi
        if (( attempt < attempts )); then
            sleep "$delay_seconds"
        fi
    done
    return 1
}

case "$ARCH" in
    simple|simple-crew)
        add_endpoint "http://localhost:${OSV_MCP_PORT}/health"
        add_endpoint "http://localhost:${SYSTEM_MCP_PORT}/health"
        add_endpoint "http://localhost:${NOTIFICATION_MCP_PORT}/health"
        add_endpoint "http://localhost:${OSV_AGENT_PORT}/.well-known/agent-card.json"
        add_endpoint "http://localhost:${MATCHER_AGENT_PORT}/.well-known/agent-card.json"
        add_endpoint "http://localhost:${NOTIFICATION_AGENT_PORT}/.well-known/agent-card.json"
        if [[ "${ENABLE_MCP_BLOAT:-false}" == "true" ]]; then
            BLOAT_MCP_PORT="${BLOAT_MCP_PORT:-9110}"
            add_endpoint "http://localhost:${BLOAT_MCP_PORT}/health"
        fi
        ;;
    gateway|gateway-rag)
        add_endpoint "http://localhost:${OSV_MCP_PORT}/health"
        add_endpoint "http://localhost:${SYSTEM_MCP_PORT}/health"
        add_endpoint "http://localhost:${NOTIFICATION_MCP_PORT}/health"
        add_endpoint "http://localhost:${GATEWAY_MCP_PORT}/health"
        add_endpoint "http://localhost:${OSV_AGENT_PORT}/.well-known/agent-card.json"
        add_endpoint "http://localhost:${MATCHER_AGENT_PORT}/.well-known/agent-card.json"
        add_endpoint "http://localhost:${NOTIFICATION_AGENT_PORT}/.well-known/agent-card.json"
        add_endpoint "http://localhost:${GATEWAY_PORT}/"
        if [[ "${ENABLE_MCP_BLOAT:-false}" == "true" ]]; then
            BLOAT_MCP_PORT="${BLOAT_MCP_PORT:-9110}"
            add_endpoint "http://localhost:${BLOAT_MCP_PORT}/health"
        fi
        ;;
    *)
        usage;;
esac

failures=0

for url in "${endpoints[@]}"; do
    if probe_endpoint "$url" "$HEALTH_CHECK_ATTEMPTS" "$HEALTH_CHECK_DELAY_SECONDS"; then
        if [[ "$QUIET" == "false" ]]; then
            echo -e "${GREEN}[OK]${RESET} $url"
        fi
    else
        failures=$((failures + 1))
        echo -e "${RED}[FAIL]${RESET} $url"
    fi
done

if [[ "$failures" -ne 0 ]]; then
    echo -e "${RED}Health check failed for ${failures} endpoint(s).${RESET}"
    exit 1
fi

if [[ "$QUIET" == "false" ]]; then
    echo -e "${GREEN}All health checks passed.${RESET}"
fi
