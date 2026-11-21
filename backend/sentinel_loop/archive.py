"""Input data archives for Sentinel-Loop.

A *work archive* packages a source data directory (the `work/` folder with
incident bundles) into one portable zip that can be downloaded on one machine
and applied on another (e.g. the Colab host):

    python -m sentinel_loop prepare-archive --src ../work
    # ... download <data-dir>/archive/*.zip ...
    python -m sentinel_loop apply-archive --archive sentinel-input-work-....zip

Archive layout (everything relative to the archive root):

    manifest.json                 # version, created_at, sha256 per file, counts
    raw/<incident_id>/...         # incident bundles (record.json, text, metrics/*.csv)
    records.jsonl                 # optional pre-ingested records
    metrics.yaml                  # optional hand-written monitoring contract
    README.md / synthesize_metrics.py  # optional provenance

`apply_archive` verifies every file against the manifest sha256, refuses
path-traversal members (zip-slip), extracts into `<data-dir>/raw/...` and
re-runs ingest. Derived pipeline outputs (labels.db, dataset/, artifacts/,
extractions/, archive/) are never included in an archive.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MANIFEST_NAME = "manifest.json"
ARCHIVE_VERSION = 1

# Top-level entries of the source dir that go into the archive.
INCLUDE_TOP = ("raw", "records.jsonl", "metrics.yaml", "README.md", "synthesize_metrics.py")
# Never archived (derived or machine-local).
EXCLUDE_NAMES = {"archive", "artifacts", "dataset", "extractions", "__pycache__", ".DS_Store",
                 "labels.db", "cost_log.jsonl", "windows.jsonl", "splits.json"}

REPO_WORK_DIR = Path(__file__).resolve().parents[2] / "work"  # <repo>/work


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def default_work_dir() -> Path:
    """The repo's work/ folder, overridable via $SENTINEL_WORK_DIR."""
    import os

    return Path(os.environ.get("SENTINEL_WORK_DIR", "")) if os.environ.get("SENTINEL_WORK_DIR") else REPO_WORK_DIR


def archive_dir_for(data_dir: str | Path) -> Path:
    return Path(data_dir) / "archive"


def make_archive(
    src_dir: str | Path,
    out_path: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> dict:
    """Zip the source work dir (raw bundles + top-level contract files) into a
    portable archive with a sha256 manifest. Returns the manifest dict."""
    src = Path(src_dir).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"source dir not found: {src}")
    if not (src / "raw").is_dir():
        raise ValueError(f"{src} has no raw/ incident bundle dir; nothing to archive")

    if out_path is None:
        base = Path(data_dir) if data_dir else src
        out_dir = archive_dir_for(base)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = out_dir / f"sentinel-input-{src.name}-{stamp}.zip"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    total = 0
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for top in INCLUDE_TOP:
            p = src / top
            if not p.exists():
                continue
            if p.is_dir():
                for f in sorted(q for q in p.rglob("*") if q.is_file()):
                    rel = f.relative_to(src).as_posix()
                    if any(part in EXCLUDE_NAMES for part in f.relative_to(src).parts):
                        continue
                    files[rel] = _sha256(f)
                    total += f.stat().st_size
                    zf.write(f, rel)
            else:
                rel = p.relative_to(src).as_posix()
                files[rel] = _sha256(p)
                total += p.stat().st_size
                zf.write(p, rel)
        manifest = {
            "version": ARCHIVE_VERSION,
            "created_at": utcnow_iso(),
            "source": src.name,
            "file_count": len(files),
            "total_bytes": total,
            "files": files,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    manifest["archive"] = out_path.name
    manifest["archive_path"] = str(out_path)
    manifest["archive_bytes"] = out_path.stat().st_size
    return manifest


def read_manifest(archive_path: str | Path) -> dict:
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            try:
                return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            except KeyError:
                raise ValueError(f"{archive_path} is not a sentinel input archive (no {MANIFEST_NAME})") from None
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{archive_path} is not a zip file: {exc}") from None


def apply_archive(
    archive_path: str | Path,
    data_dir: str | Path,
    ingest: bool = True,
) -> dict:
    """Verify + extract an input archive into <data-dir>/, then ingest raw/.

    Every manifest entry is checked: the member must exist, must not escape
    <data-dir> (zip-slip), and its sha256 must match. Returns a summary dict.
    """
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise FileNotFoundError(f"archive not found: {archive_path}")
    data_dir = Path(data_dir).resolve()
    manifest = read_manifest(archive_path)
    if manifest.get("version") != ARCHIVE_VERSION:
        raise ValueError(f"unsupported archive version {manifest.get('version')!r}")
    files = manifest.get("files") or {}
    if not files:
        raise ValueError("archive manifest lists no files")

    data_dir.mkdir(parents=True, exist_ok=True)
    root = data_dir.resolve()
    extracted: list[str] = []
    try:
        zf = zipfile.ZipFile(archive_path, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{archive_path} is not a zip file: {exc}") from None
    with zf:
        names = set(zf.namelist())
        for rel, expected_sha in files.items():
            if rel not in names:
                raise ValueError(f"archive is missing manifest entry {rel!r}")
            target = (root / rel).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"refusing zip-slip member {rel!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(rel) as src_fh, open(target, "wb") as out_fh:
                import shutil

                shutil.copyfileobj(src_fh, out_fh)
            if _sha256(target) != expected_sha:
                target.unlink(missing_ok=True)
                raise ValueError(f"sha256 mismatch for {rel!r}; archive corrupted or tampered")
            extracted.append(rel)

    result = {
        "archive": archive_path.name,
        "source": manifest.get("source"),
        "created_at": manifest.get("created_at"),
        "files_extracted": len(extracted),
        "data_dir": str(data_dir),
        "ingested": False,
        "records": 0,
    }
    if ingest:
        from .ingest.loader import ingest_directory

        records = ingest_directory(data_dir / "raw", data_dir)
        result["ingested"] = True
        result["records"] = len(records)
    return result


def list_archives(data_dir: str | Path) -> list[dict]:
    """Summaries of all input archives in <data-dir>/archive/."""
    out: list[dict] = []
    adir = archive_dir_for(data_dir)
    if not adir.is_dir():
        return out
    for z in sorted(adir.glob("*.zip")):
        try:
            m = read_manifest(z)
        except Exception:  # noqa: BLE001 - list must never fail on one bad file
            out.append({"name": z.name, "bytes": z.stat().st_size, "valid": False})
            continue
        out.append({
            "name": z.name,
            "bytes": z.stat().st_size,
            "valid": True,
            "created_at": m.get("created_at"),
            "source": m.get("source"),
            "file_count": m.get("file_count"),
            "total_bytes": m.get("total_bytes"),
        })
    return out
