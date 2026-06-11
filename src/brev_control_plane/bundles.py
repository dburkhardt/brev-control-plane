from __future__ import annotations

from pathlib import Path
import tarfile
from typing import Iterable


class BundleError(ValueError):
    """Raised when a job bundle cannot be packaged."""


def create_bundle_archive(
    source_dir: str | Path,
    output_path: str | Path,
    *,
    exclude_names: Iterable[str] | None = None,
) -> Path:
    source = Path(source_dir)
    if not source.is_dir():
        raise BundleError("source_dir must be a directory")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    excluded = set(exclude_names or [])

    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in excluded for part in relative.parts):
                continue
            if path.is_file():
                archive.add(path, arcname=str(relative))
    return output
