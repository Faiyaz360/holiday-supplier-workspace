"""Fixed-name exact-byte source evidence, with backwards-compatible resolution."""

import hashlib
from pathlib import Path

SOURCE_FILES = {"source-statement.csv", "source-ledger.csv"}


def save_sources(folder: Path, statement: bytes, ledger: bytes) -> None:
    for name, raw in (("source-statement.csv", statement), ("source-ledger.csv", ledger)):
        if len(raw) > 2_000_000:
            raise ValueError("Saved source exceeds its size limit")
        raw.decode("utf-8-sig")
        (folder / name).write_bytes(raw)


def validate_sources(folder: Path, meta: dict) -> bool:
    present = {name for name in SOURCE_FILES if (folder / name).exists()}
    declared = "source_evidence_version" in meta or "source_kind" in meta
    if not declared and not present:
        return False
    if (
        type(meta.get("source_evidence_version")) is not int
        or meta["source_evidence_version"] != 1
        or meta.get("source_kind") not in {"fixture", "uploaded"}
        or present != SOURCE_FILES
    ):
        raise ValueError("Saved source manifest integrity check failed")
    for key, name in (("statement", "source-statement.csv"), ("ledger", "source-ledger.csv")):
        path = folder / name
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ValueError("Saved source integrity check failed")
        raw = path.read_bytes()
        raw.decode("utf-8-sig")
        if hashlib.sha256(raw).hexdigest() != meta.get("input_sha256", {}).get(key):
            raise ValueError("Saved source integrity check failed")
    return True


def resolve_sources(
    run_id: str, output: Path, fallback_statement: Path, fallback_ledger: Path
) -> tuple[Path, Path]:
    from .runs import load_run, run_folder

    data = load_run(run_id, output)
    folder = run_folder(run_id, output)
    if validate_sources(folder, data["meta"]):
        return folder / "source-statement.csv", folder / "source-ledger.csv"
    return fallback_statement, fallback_ledger
