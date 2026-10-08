"""
Download and install the SBOM dataset from Zenodo when preparation runs.
"""
from __future__ import annotations
import json
import logging
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
import httpx

DEFAULT_SBOM_DATASET_RECORD_ID = "14808410"
ZENODO_RECORD_API_URL = "https://zenodo.org/api/records/{record_id}"
REPOSITORIES_FILENAME = "repositories.json"
SPDX_FILENAME = "sbom.spdx.json"
LOGGER = logging.getLogger(__name__)


def _dataset_is_ready(dataset_root: Path) -> bool:
    """Check whether the dataset already exists in the expected shape."""
    if not dataset_root.is_dir():
        return False
    if not (dataset_root / REPOSITORIES_FILENAME).is_file():
        return False
    for project_dir in dataset_root.iterdir():
        if project_dir.is_dir() and (project_dir / SPDX_FILENAME).is_file():
            return True
    return False


def _pick_archive(record_payload: dict[str, object]) -> tuple[str, str]:
    """Pick the largest archive entry from the Zenodo files list."""
    archive_files = [item for item in record_payload["files"] if str(item["key"]).lower().endswith((".zip", ".tar.gz", ".tgz", ".tar"))]
    archive_entry = max(
        archive_files,
        key=lambda item: int(item.get("size") or 0),
    )
    archive_name = Path(archive_entry["key"]).name
    archive_url = archive_entry["links"].get("content") or archive_entry["links"].get("self") or archive_entry["links"].get("download")
    if archive_url is None:
        raise KeyError("No archive download URL in Zenodo file links")
    return archive_name, archive_url


def _resolve_dataset_source(extract_root: Path) -> Path:
    """Resolve the top folder that contains repositories.json."""
    if (extract_root / REPOSITORIES_FILENAME).is_file():
        return extract_root
    for entry in extract_root.iterdir():
        if entry.is_dir() and (entry / REPOSITORIES_FILENAME).is_file():
            return entry
    raise FileNotFoundError(f"Could not find {REPOSITORIES_FILENAME} in extracted archive")


def ensure_sbom_dataset(
    dataset_root: Path,
    timeout_s: float,
    record_id: str = DEFAULT_SBOM_DATASET_RECORD_ID,
    refresh: bool = False,
    logger: logging.Logger | None = None,
) -> Path:
    """
    Ensure the SBOM dataset exists in the expected structure.
    Downloads and extracts the dataset from Zenodo when needed.
    """
    active_logger = logger or LOGGER
    if _dataset_is_ready(dataset_root) and not refresh:
        active_logger.info(f"SBOM dataset is ready at {dataset_root}")
        return dataset_root
    try:
        normalized_record_id = record_id.strip()
        if len(normalized_record_id) == 0:
            raise ValueError("Zenodo SBOM record id must not be empty")
        record_url = ZENODO_RECORD_API_URL.format(record_id=normalized_record_id)
        active_logger.info(f"Preparing SBOM dataset from Zenodo record {normalized_record_id}")
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            record_response = client.get(
                url=record_url,
                headers={"Accept": "application/json"},
            )
            record_response.raise_for_status()
            record_payload = json.loads(record_response.text)
            archive_name, archive_url = _pick_archive(record_payload=record_payload)
            active_logger.info(f"Downloading SBOM archive {archive_name}")
            with tempfile.TemporaryDirectory(prefix="sbom-dataset-") as temp_dir:
                temp_root = Path(temp_dir)
                archive_path = temp_root / archive_name
                extract_root = temp_root / "extracted"
                extract_root.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                with client.stream(
                    method="GET",
                    url=archive_url,
                ) as archive_response:
                    archive_response.raise_for_status()
                    with archive_path.open("wb") as archive_file:
                        for chunk in archive_response.iter_bytes():
                            archive_file.write(chunk)
                if archive_name.lower().endswith(".zip"):
                    with zipfile.ZipFile(archive_path) as zip_handle:
                        zip_handle.extractall(extract_root)
                else:
                    with tarfile.open(archive_path) as tar_handle:
                        tar_handle.extractall(extract_root)
                dataset_source = _resolve_dataset_source(extract_root=extract_root)
                dataset_root.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                if dataset_root.exists():
                    shutil.rmtree(dataset_root)
                shutil.copytree(
                    src=dataset_source,
                    dst=dataset_root,
                )
        if not _dataset_is_ready(dataset_root):
            raise RuntimeError(f"Downloaded dataset at {dataset_root} is incomplete")
        active_logger.info(f"SBOM dataset prepared at {dataset_root}")
        return dataset_root
    except Exception as exc:
        active_logger.error(f"SBOM dataset preparation failed: {exc}", exc_info=True)
        raise
