"""
The pipeline version (ADR-0006, Lineage): a hash of everything that shapes an export, so the same inputs always carry
the same version on every machine, and a change to any of them gets a new export prefix instead of meeting the bucket's
refusal to overwrite. dbt's working directories and the project's README don't shape an export.
"""

import hashlib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SHAPES = (
    "pipeline",
    "src/faro/pipeline",
    "src/faro/clock.py",
    "src/faro/export/items.py",
    "src/faro/contracts/tools-data.schema.json",
)
PACKAGES = ("dbt-core", "dbt-duckdb", "duckdb")
SKIPPED = {"target", "logs", "dbt_packages", "__pycache__", "README.md"}


def _shaping(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return not any(part in SKIPPED or part.startswith(".") for part in parts) and (
        path.suffix != ".pyc"
    )


def files(root: Path = ROOT) -> list[Path]:
    found = []
    for shape in SHAPES:
        path = root / shape
        candidates = [path] if path.is_file() else path.rglob("*")
        found += [p for p in candidates if p.is_file() and _shaping(p, root)]
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def versions() -> dict[str, str]:
    return {package: metadata.version(package) for package in PACKAGES}


def pipeline_version(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for path in files(root):
        digest.update(f"{path.relative_to(root).as_posix()}\n".encode())
        digest.update(path.read_bytes())
    for package, installed in versions().items():
        digest.update(f"{package} {installed}\n".encode())
    return digest.hexdigest()[:16]
