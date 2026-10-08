"""
Command-line interface for preparation and evaluation modes.
"""
from __future__ import annotations
import argparse
import logging
from pathlib import Path
from evaluation.eval_runner import run_evaluation
from evaluation.prep import run_preparation


def _parse_args() -> argparse.Namespace:
    """Build and parse CLI arguments."""
    parser = argparse.ArgumentParser(description="New evaluation pipeline entrypoint")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prep", help="Build scenarios and cache OSV responses")
    prep.add_argument("--scenario-config", default="evaluation/config/scenarios.json", help="Path to scenario config JSON")
    prep.add_argument("--sbom-dataset", default="evaluation/data/persistent/sbom-dataset", help="Path to SBOM dataset root")
    prep.add_argument("--output-dir", default="evaluation/data/tmp/scenarios", help="Directory for generated scenarios")
    prep.add_argument("--osv-cache", default="evaluation/data/persistent/cache/osv", help="Cache directory for OSV responses")
    prep.add_argument("--seed", type=int, default=13, help="Seed for deterministic sampling")
    prep.add_argument("--sbom-record-id", default="14808410", help="Zenodo record id used to fetch the SBOM dataset")
    prep.add_argument(
        "--sbom-download-timeout",
        type=float,
        default=600,
        help="Timeout seconds for dataset download calls",
    )
    prep.add_argument("--refresh-sbom-dataset", action="store_true", help="Force re-download of the SBOM dataset")
    prep.add_argument("--refresh-cache", action="store_true", help="Force refresh of OSV cache entries")
    prep.add_argument("--timeout", type=float, default=180.0, help="HTTP timeout seconds")
    prep.add_argument("--retries", type=int, default=1, help="Retry attempts for OSV queries")
    prep.add_argument("--backoff", type=float, default=0.5, help="Backoff base seconds for retries")
    prep.add_argument(
        "--owners",
        default="evaluation/config/system_owners.json",
        help="Path to system_owners.json fixture",
    )

    run = sub.add_parser("run", help="Iterate scenarios and trigger the pipeline")
    run.add_argument("--manifest", default="evaluation/data/tmp/scenarios/manifest.json", help="Path to scenario manifest")
    run.add_argument("--scenario-id", default=None, help="Optional single scenario id to run")
    run.add_argument(
        "--arch",
        default=None,
        choices=["simple", "simple-crew", "gateway", "gateway-rag"],
        help="Pipeline architecture label for result metadata",
    )
    run.add_argument("--agent-url", required=True, help="Target agent URL")
    run.add_argument("--skill", default="get_osv_report", help="Skill name to execute")
    run.add_argument("--run-output", default="evaluation/data/tmp/results/runs", help="Directory for run metadata")
    return parser.parse_args()


def main() -> int:
    """Dispatch to preparation or evaluation workflows."""
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("evaluation")

    if args.command == "prep":
        run_preparation(
            config_path=Path(args.scenario_config),
            sbom_dataset=Path(args.sbom_dataset),
            cache_dir=Path(args.osv_cache),
            output_root=Path(args.output_dir),
            seed=int(args.seed),
            refresh_cache=bool(args.refresh_cache),
            timeout_s=float(args.timeout),
            retries=int(args.retries),
            backoff_s=float(args.backoff),
            sbom_download_timeout_s=float(args.sbom_download_timeout),
            sbom_record_id=str(args.sbom_record_id),
            refresh_sbom_dataset=bool(args.refresh_sbom_dataset),
            owners_path=Path(args.owners) if args.owners else None,
            logger=logger,
        )
        return 0

    run_evaluation(
        manifest_path=Path(args.manifest),
        scenario_id=str(args.scenario_id) if args.scenario_id else None,
        architecture=str(args.arch) if args.arch else None,
        agent_url=str(args.agent_url),
        skill=str(args.skill),
        run_output_dir=Path(args.run_output),
        logger=logger,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
