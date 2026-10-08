#!/usr/bin/env bash
# Run evaluation preparation (scenario generation + cache warmup).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<EOF
Usage: $0 [--test]
EOF
    exit 1
}

check_uv
load_env

TEST_MODE="false"

while [[ $# -gt 0 ]]; do
    case $1 in
        --test)
            TEST_MODE="true"; shift 1;;
        *)
            usage;;
    esac
done

scenario_config="$ROOT_DIR/evaluation/config/scenarios.json"
if [[ "$TEST_MODE" == "true" ]]; then
    scenario_config="$ROOT_DIR/evaluation/config/test-scenarios.json"
fi

tmp_scenarios_dir="$ROOT_DIR/evaluation/data/tmp/scenarios"
rm -rf "$tmp_scenarios_dir"

uv run python -m evaluation.cli prep \
    --scenario-config "$scenario_config" \
    --sbom-dataset "$ROOT_DIR/evaluation/data/persistent/sbom-dataset" \
    --output-dir "$ROOT_DIR/evaluation/data/tmp/scenarios" \
    --osv-cache "$ROOT_DIR/evaluation/data/persistent/cache/osv" \
    --seed 13 \
    --timeout 20 \
    --retries 1 \
    --backoff 0.5
