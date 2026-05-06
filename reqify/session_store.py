from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, ensure_dirs, safe_name
from .git_repo import commit_repo, init_repo, run_git
from .reqif_document import ReqifDocument


def safe_extract(zip_file: zipfile.ZipFile, target: Path) -> None:
    target_resolved = target.resolve()
    for info in zip_file.infolist():
        destination = (target / info.filename).resolve()
        if not str(destination).startswith(str(target_resolved)):
            raise ValueError(f"Unsafe archive member: {info.filename}")
    zip_file.extractall(target)


def session_dir(session_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise ValueError("Invalid session id")
    return DATA_DIR / session_id


def session_meta(session_id: str) -> dict[str, object]:
    meta_path = session_dir(session_id) / "session.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


def write_session_meta(session_id: str, meta: dict[str, object]) -> None:
    (session_dir(session_id) / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def repo_dir(session_id: str) -> Path:
    return session_dir(session_id) / "repo"


def document_path(session_id: str) -> Path:
    meta = session_meta(session_id)
    return repo_dir(session_id) / str(meta["documentRel"])


def load_payload(session_id: str) -> dict[str, object]:
    meta = session_meta(session_id)
    document = ReqifDocument(document_path(session_id))
    payload = document.as_payload()
    payload["sessionId"] = session_id
    payload["fileName"] = meta["originalName"]
    payload["history"] = history_payload(session_id)
    return payload


def history_payload(session_id: str) -> list[dict[str, str]]:
    result = run_git(
        repo_dir(session_id),
        "log",
        "--pretty=format:%H%x00%h%x00%ci%x00%s",
        "--",
        ".",
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    history = []
    for line in result.stdout.splitlines():
        full, short, date, subject = line.split("\x00", 3)
        history.append({"hash": full, "short": short, "date": date, "subject": subject})
    return history


def create_session(filename: str, content: bytes) -> dict[str, object]:
    ensure_dirs()
    session_id = uuid.uuid4().hex
    sdir = session_dir(session_id)
    repo = repo_dir(session_id)
    repo.mkdir(parents=True)
    original_name = safe_name(filename)
    is_zip = original_name.lower().endswith(".reqifz") or zipfile.is_zipfile(io.BytesIO(content))
    if is_zip:
        archive_path = sdir / original_name
        archive_path.write_bytes(content)
        with zipfile.ZipFile(archive_path) as archive:
            safe_extract(archive, repo)
        candidates = sorted([path for path in repo.rglob("*") if path.is_file() and path.suffix.lower() in {".reqif", ".xml"}])
        if not candidates:
            raise ValueError("The archive does not contain a .reqif or .xml document")
        document_rel = str(candidates[0].relative_to(repo))
    else:
        document_name = original_name if Path(original_name).suffix else f"{original_name}.reqif"
        (repo / document_name).write_bytes(content)
        document_rel = document_name
    init_repo(repo)
    meta = {
        "id": session_id,
        "originalName": original_name,
        "isZip": is_zip,
        "documentRel": document_rel,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    write_session_meta(session_id, meta)
    commit_repo(repo, "Import original ReqIF")
    return load_payload(session_id)


def save_session(session_id: str, updates: dict[str, object]) -> dict[str, object]:
    document = ReqifDocument(document_path(session_id))
    document.apply_updates(updates)
    document.write()
    committed = commit_repo(repo_dir(session_id), f"Save edits {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    payload = load_payload(session_id)
    payload["committed"] = committed
    return payload


def edited_export_name(original_name: str, fallback_suffix: str) -> str:
    path = Path(original_name)
    suffix = path.suffix or fallback_suffix
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    return f"{stem}-edited{suffix}"


def export_session(session_id: str) -> tuple[str, bytes, str]:
    meta = session_meta(session_id)
    repo = repo_dir(session_id)
    original_name = str(meta["originalName"])
    if meta.get("isZip"):
        export_name = edited_export_name(original_name, ".reqifz")
        export_path = session_dir(session_id) / export_name
        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in repo.rglob("*"):
                if path.is_file() and ".git" not in path.parts:
                    archive.write(path, path.relative_to(repo))
        return export_name, export_path.read_bytes(), "application/zip"
    export_name = edited_export_name(original_name, ".reqif")
    return export_name, document_path(session_id).read_bytes(), "application/xml"

