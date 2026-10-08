#!/usr/bin/env bash
# Run evaluation against an already prepared manifest.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/services.sh"

usage() {
    cat <<EOF
Usage: $0 --arch <simple|simple-crew|gateway|gateway-rag> [--mcp-bloat] [--mcp-bloat-tools <count>]
EOF
    exit 1
}

ARCH=""
CLI_MCP_BLOAT="false"
CLI_MCP_BLOAT_TOOLS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --arch)
            ARCH="$2"; shift 2;;
        --mcp-bloat)
            CLI_MCP_BLOAT="true"; shift 1;;
        --mcp-bloat-tools)
            CLI_MCP_BLOAT_TOOLS="$2"; shift 2;;
        *)
            usage;;
    esac
done

if [[ -z "$ARCH" ]]; then
    usage
fi
if [[ -n "$CLI_MCP_BLOAT_TOOLS" && "$CLI_MCP_BLOAT" != "true" ]]; then
    echo "--mcp-bloat-tools requires --mcp-bloat." >&2
    usage
fi
if [[ -n "$CLI_MCP_BLOAT_TOOLS" && ! "$CLI_MCP_BLOAT_TOOLS" =~ ^[0-9]+$ ]]; then
    echo "--mcp-bloat-tools must be an integer >= 0." >&2
    usage
fi

check_uv
load_env

ENABLE_MCP_BLOAT="false"
if [[ "$CLI_MCP_BLOAT" == "true" ]]; then
    ENABLE_MCP_BLOAT="true"
fi
export ENABLE_MCP_BLOAT

OSV_AGENT_PORT=${OSV_AGENT_PORT:-9200}
if [[ "$ENABLE_MCP_BLOAT" == "true" ]]; then
    if [[ -n "$CLI_MCP_BLOAT_TOOLS" ]]; then
        MCP_BLOAT_TOOL_COUNT="$CLI_MCP_BLOAT_TOOLS"
    elif [[ -z "${MCP_BLOAT_TOOL_COUNT:-}" ]]; then
        MCP_BLOAT_TOOL_COUNT="-1"
    fi
    if [[ ! "${MCP_BLOAT_TOOL_COUNT:-}" =~ ^-?[0-9]+$ ]]; then
        echo "MCP_BLOAT_TOOL_COUNT must be an integer (use -1 for all tools)." >&2
        exit 1
    fi
    if (( MCP_BLOAT_TOOL_COUNT < -1 )); then
        echo "MCP_BLOAT_TOOL_COUNT must be >= -1." >&2
        exit 1
    fi
    export MCP_BLOAT_TOOL_COUNT
    BLOAT_MCP_PORT=${BLOAT_MCP_PORT:-9110}
    export BLOAT_MCP_PORT
fi
manifest_path="$ROOT_DIR/evaluation/data/tmp/scenarios/manifest.json"
if [[ ! -f "$manifest_path" ]]; then
    echo -e "${RED}Manifest not found at ${manifest_path}${RESET}"
    exit 1
fi

trap cleanup EXIT TERM
trap handle_interrupt INT

start_services "$ARCH"

agent_url="http://localhost:${OSV_AGENT_PORT}"
run_output="$ROOT_DIR/evaluation/data/tmp/results/runs"

echo -e "\n${BOLD}Running evaluation...${RESET}"
uv run python -m evaluation.cli run \
    --manifest "$manifest_path" \
    --arch "$ARCH" \
    --agent-url "$agent_url" \
    --skill "get_osv_report" \
    --run-output "$run_output"
