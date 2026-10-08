#!/usr/bin/env bash
# Start required services for a given architecture.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"

run_mcp_server() {
    local mcp_dir="$ROOT_DIR/mcp-server"
    echo -e "\n${YELLOW}Starting MCP servers...${RESET}"
    run_service "OSV MCP" uv run python "$mcp_dir/osv_mcp_server.py" --port "$OSV_MCP_PORT"
    run_service "System MCP" uv run python "$mcp_dir/system_mcp_server.py" --port "$SYSTEM_MCP_PORT"
    run_service "Notification MCP" uv run python "$mcp_dir/notification_mcp_server.py" --port "$NOTIFICATION_MCP_PORT"
    if [[ "${ENABLE_MCP_BLOAT:-false}" == "true" ]]; then
        BLOAT_MCP_PORT="${BLOAT_MCP_PORT:-9110}"
        MCP_BLOAT_TOOL_COUNT="${MCP_BLOAT_TOOL_COUNT:--1}"
        export BLOAT_MCP_PORT
        export MCP_BLOAT_TOOL_COUNT
        echo -e "\n${YELLOW}Starting bloat MCP server...${RESET}"
        run_service "Bloat MCP" bash -c "cd \"$mcp_dir\" && exec uv run python -m bloat.bloat_mcp_server --port \"$BLOAT_MCP_PORT\" --tool-count \"$MCP_BLOAT_TOOL_COUNT\""
    fi
    sleep 5
}

start_simple() {
    local mode="$1"
    run_mcp_server
    run_service "OsvAgent" uv run python -m simple.agents.osv_agent --port "$OSV_AGENT_PORT" --arch "$mode"
    run_service "MatcherAgent" uv run python -m simple.agents.matcher_agent --port "$MATCHER_AGENT_PORT" --arch "$mode"
    run_service "NotificationAgent" uv run python -m simple.agents.notification_agent --port "$NOTIFICATION_AGENT_PORT" --arch "$mode"
    sleep 5
}

start_gateway() {
    local mode="$1"
    local mode_args=("--mode" "$mode")
    run_mcp_server
    run_service "OSV Agent" uv run python -m gateway.agents.osv_agent.osv_agent --port "$OSV_AGENT_PORT" "${mode_args[@]}"
    run_service "Matcher Agent" uv run python -m gateway.agents.matcher_agent.matcher_agent --port "$MATCHER_AGENT_PORT" "${mode_args[@]}"
    run_service "Notification Agent" uv run python -m gateway.agents.notification_agent.notification_agent --port "$NOTIFICATION_AGENT_PORT" "${mode_args[@]}"
    run_service "Gateway MCP" uv run python "$ROOT_DIR/pipelines/gateway/mcp-server/gateway_mcp_server.py" --port "$GATEWAY_MCP_PORT" "${mode_args[@]}"
    sleep 10
    run_service "Gateway" uv run python -m gateway.gateway.gateway --port "$GATEWAY_PORT" "${mode_args[@]}"
    sleep 5
    if [[ "$mode" == "RAG" ]]; then
        echo -e "${YELLOW}Extra wait for VectorDB (10s)...${RESET}"
        sleep 10
    fi
}

start_services() {
    local arch="$1"
    local requested_arch="$1"
    local mode="standard"
    if [[ "$arch" == "gateway-rag" ]]; then
        mode="RAG"
        arch="gateway"
    fi
    case "$arch" in
        simple)
            start_simple "simple"
            ;;
        simple-crew)
            start_simple "crew"
            ;;
        gateway)
            start_gateway "$mode"
            ;;
        *)
            echo -e "${RED}Invalid --arch: ${arch}${RESET}"
            exit 1
            ;;
    esac
    if [[ "${ENABLE_MCP_BLOAT:-false}" == "true" ]]; then
        "$SCRIPT_DIR/health_check.sh" --arch "$requested_arch" --quiet --mcp-bloat
        return
    fi
    "$SCRIPT_DIR/health_check.sh" --arch "$requested_arch" --quiet
}
