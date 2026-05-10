from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, ensure_dirs, safe_name
from .git_repo import commit_repo, init_repo, run_git, run_git_bytes
from .reqif_document import ReqifDocument
from .xml_utils import attribute_key, strip_markup


UI_STATE_REL = ".reqify-state.json"
EXPORTS_DIR = "exports"


def safe_extract(zip_file: zipfile.ZipFile, target: Path) -> None:
    validate_archive_members(zip_file, target)
    zip_file.extractall(target)


def validate_archive_members(zip_file: zipfile.ZipFile, target: Path) -> None:
    target_resolved = target.resolve()
    for info in zip_file.infolist():
        destination = (target / info.filename).resolve()
        if destination != target_resolved and target_resolved not in destination.parents:
            raise ValueError(f"Unsafe archive member: {info.filename}")


def uploaded_document_bytes(original_name: str, content: bytes) -> bytes:
    is_zip = original_name.lower().endswith(".reqifz") or zipfile.is_zipfile(io.BytesIO(content))
    if not is_zip:
        return content
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        validate_archive_members(archive, Path(tempfile.gettempdir()) / "reqify-upload-fingerprint")
        candidates = sorted(
            [
                info.filename
                for info in archive.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() in {".reqif", ".xml"}
            ]
        )
        if not candidates:
            raise ValueError("The archive does not contain a .reqif or .xml document")
        return archive.read(candidates[0])


def source_sha256(original_name: str, content: bytes) -> str:
    return hashlib.sha256(uploaded_document_bytes(original_name, content)).hexdigest()


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


def ui_state_path(session_id: str) -> Path:
    return repo_dir(session_id) / UI_STATE_REL


def head_commit(session_id: str) -> str:
    result = run_git(repo_dir(session_id), "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def load_payload(session_id: str) -> dict[str, object]:
    meta = session_meta(session_id)
    document = ReqifDocument(document_path(session_id))
    payload = document.as_payload()
    payload["sessionId"] = session_id
    payload["fileName"] = meta["originalName"]
    payload["headCommit"] = head_commit(session_id)
    payload["history"] = history_payload(session_id)
    payload["uiState"] = load_ui_state(session_id)
    return payload


def load_ui_state(session_id: str) -> dict[str, object]:
    path = ui_state_path(session_id)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_ui_state(session_id: str, ui_state: object) -> None:
    state = ui_state if isinstance(ui_state, dict) else {}
    ui_state_path(session_id).write_text(json.dumps(state, indent=2), encoding="utf-8")


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


def existing_session_for_source(source_hash: str) -> str | None:
    if not DATA_DIR.exists():
        return None
    matches: list[tuple[str, str]] = []
    for meta_path in DATA_DIR.glob("*/session.json"):
        session_id = meta_path.parent.name
        if not re.fullmatch(r"[a-f0-9]{32}", session_id):
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta_hash = str(meta.get("sourceSha256") or "")
            if not meta_hash:
                meta_hash = legacy_source_sha256(session_id, meta)
                if meta_hash:
                    meta["sourceSha256"] = meta_hash
                    write_session_meta(session_id, meta)
            if meta_hash == source_hash:
                matches.append((str(meta.get("createdAt") or ""), session_id))
        except Exception:
            continue
    if not matches:
        return None
    return sorted(matches)[-1][1]


def legacy_source_sha256(session_id: str, meta: dict[str, object]) -> str:
    repo = repo_dir(session_id)
    document_rel = str(meta.get("documentRel") or "")
    if not document_rel:
        return ""
    root = run_git(repo, "rev-list", "--max-parents=0", "HEAD", check=False)
    if root.returncode != 0 or not root.stdout.strip():
        return ""
    first_commit = root.stdout.splitlines()[0].strip()
    document = run_git_bytes(repo, "show", f"{first_commit}:{document_rel}", check=False)
    if document.returncode != 0:
        return ""
    return hashlib.sha256(document.stdout).hexdigest()


def create_session(filename: str, content: bytes) -> dict[str, object]:
    ensure_dirs()
    original_name = safe_name(filename)
    source_hash = source_sha256(original_name, content)
    existing_session_id = existing_session_for_source(source_hash)
    if existing_session_id:
        payload = load_payload(existing_session_id)
        payload["reusedSession"] = True
        return payload
    session_id = uuid.uuid4().hex
    sdir = session_dir(session_id)
    repo = repo_dir(session_id)
    repo.mkdir(parents=True)
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
        "sourceSha256": source_hash,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    write_session_meta(session_id, meta)
    write_ui_state(session_id, {})
    commit_repo(repo, "Import original ReqIF")
    return load_payload(session_id)


def save_session(session_id: str, updates: dict[str, object], viewed_commit: str, ui_state: object | None = None) -> dict[str, object]:
    current_head = head_commit(session_id)
    if current_head and viewed_commit != current_head:
        raise ValueError("Save is only allowed while viewing HEAD. Checkout the latest commit before saving.")
    if updates:
        document = ReqifDocument(document_path(session_id))
        document.apply_updates(updates)
        document.write()
    if ui_state is not None:
        write_ui_state(session_id, ui_state)
    committed = commit_repo(repo_dir(session_id), f"Save edits {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    payload = load_payload(session_id)
    payload["committed"] = committed
    return payload


def object_text_at_commit(session_id: str, commit: str, object_id: str) -> dict[str, str]:
    payload = payload_at_commit(session_id, commit)
    objects = payload.get("objects", {})
    if not isinstance(objects, dict):
        raise ValueError("Could not read objects at the selected commit.")
    selected = objects.get(object_id)
    if not isinstance(selected, dict):
        raise ValueError("The selected requirement does not exist at that commit.")
    attributes = selected.get("attributes", [])
    if not isinstance(attributes, list):
        raise ValueError("The selected requirement has no text at that commit.")
    text_attr = next((attr for attr in attributes if isinstance(attr, dict) and attribute_key(attr) == "reqiftext"), None)
    if text_attr is None:
        raise ValueError("The selected requirement has no ReqIF.Text at that commit.")
    return {
        "objectId": object_id,
        "commit": commit,
        "text": strip_markup(str(text_attr.get("value", ""))),
    }


def payload_at_commit(session_id: str, commit: str) -> dict[str, object]:
    meta = session_meta(session_id)
    document_rel = str(meta["documentRel"])
    result = run_git(repo_dir(session_id), "show", f"{commit}:{document_rel}", check=False)
    if result.returncode != 0:
        raise ValueError("Could not load document at the selected commit.")
    suffix = Path(document_rel).suffix or ".reqif"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=suffix, delete=False) as temp:
            temp.write(result.stdout)
            temp_path = Path(temp.name)
        document = ReqifDocument(temp_path)
        payload = document.as_payload()
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    payload["commit"] = commit
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
                if path.is_file() and ".git" not in path.parts and path.relative_to(repo).as_posix() != UI_STATE_REL:
                    archive.write(path, path.relative_to(repo))
        return export_name, export_path.read_bytes(), "application/zip"
    export_name = edited_export_name(original_name, ".reqif")
    return export_name, document_path(session_id).read_bytes(), "application/xml"


def export_artifact_dir(session_id: str) -> Path:
    return session_dir(session_id) / EXPORTS_DIR


def create_export_artifact(session_id: str) -> dict[str, str]:
    export_name, body, content_type = export_session(session_id)
    export_id = uuid.uuid4().hex
    target = export_artifact_dir(session_id)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{export_id}.bin").write_bytes(body)
    (target / f"{export_id}.json").write_text(
        json.dumps(
            {
                "name": export_name,
                "contentType": content_type,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "downloadUrl": f"/api/session/{session_id}/export/{export_id}",
        "fileName": export_name,
    }


def load_export_artifact(session_id: str, export_id: str) -> tuple[str, bytes, str]:
    if not re.fullmatch(r"[a-f0-9]{32}", export_id):
        raise ValueError("Invalid export id")
    target = export_artifact_dir(session_id)
    meta_path = target / f"{export_id}.json"
    body_path = target / f"{export_id}.bin"
    if not meta_path.is_file() or not body_path.is_file():
        raise ValueError("Export not found")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    name = str(meta.get("name") or "export.reqif")
    content_type = str(meta.get("contentType") or "application/octet-stream")
    return name, body_path.read_bytes(), content_type
