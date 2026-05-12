from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree as ET

from .agent import AgentBackendError, AgentRequest, analyze_agent
from .config import STATIC_DIR, ensure_dirs
from .git_repo import run_git
from .http_utils import json_bytes, parse_multipart
from .jobs import job_payload, start_job
from .session_store import (
    create_session,
    create_export_artifact,
    create_filtered_export_artifact,
    export_session,
    history_payload,
    load_attachment,
    load_export_artifact,
    load_payload,
    object_text_at_commit,
    payload_at_commit,
    repo_dir,
    save_session,
)


class ClientDisconnected(Exception):
    pass


def checkout_session_commit(session_id: str, commit: str) -> dict[str, object]:
    run_git(repo_dir(session_id), "checkout", commit, "--", ".")
    return load_payload(session_id)


class ReqifyHandler(BaseHTTPRequestHandler):
    server_version = "Reqify/0.1"

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def send_bytes(self, body: bytes, status: HTTPStatus = HTTPStatus.OK, content_type: str = "application/octet-stream") -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ClientDisconnected() from exc

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json_bytes(payload), status, "application/json; charset=utf-8")

    def send_attachment(self, name: str, body: bytes, content_type: str) -> None:
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ClientDisconnected() from exc

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def send_internal_error(self, message: str) -> None:
        try:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, message)
        except ClientDisconnected:
            self.log_client_disconnect()

    def log_client_disconnect(self) -> None:
        if os.environ.get("REQIFY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
            sys.stderr.write(f"Client disconnected before response completed: {self.command} {self.path}\n")

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/":
                self.serve_file(STATIC_DIR / "index.html")
            elif path.startswith("/static/"):
                self.serve_file(STATIC_DIR / path.removeprefix("/static/"))
            elif re.fullmatch(r"/api/session/[a-f0-9]{32}/export/[a-f0-9]{32}", path):
                _, _, session_id, _, export_id = path.strip("/").split("/")
                name, body, content_type = load_export_artifact(session_id, export_id)
                self.send_attachment(name, body, content_type)
            elif re.fullmatch(r"/api/session/[a-f0-9]{32}/attachment", path):
                session_id = path.split("/")[3]
                query = parse_qs(parsed.query)
                rel_path = query.get("path", [""])[0]
                if not rel_path:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Missing attachment path")
                    return
                name, body, content_type = load_attachment(session_id, rel_path)
                self.send_attachment(name, body, content_type)
            elif path.startswith("/api/session/") and path.endswith("/export"):
                session_id = path.split("/")[3]
                name, body, content_type = export_session(session_id)
                self.send_attachment(name, body, content_type)
            elif path.startswith("/api/session/") and path.endswith("/history"):
                session_id = path.split("/")[3]
                self.send_json({"history": history_payload(session_id)})
            elif path.startswith("/api/job/"):
                job_id = path.split("/")[3]
                payload = job_payload(job_id)
                if payload is None:
                    self.send_error_json(HTTPStatus.NOT_FOUND, "Job not found")
                    return
                self.send_json(payload)
            elif path.startswith("/api/session/"):
                session_id = path.split("/")[3]
                self.send_json(load_payload(session_id))
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except ClientDisconnected:
            self.log_client_disconnect()
        except Exception as exc:
            self.send_internal_error(str(exc))

    def serve_file(self, path: Path) -> None:
        resolved = path.resolve()
        if not str(resolved).startswith(str(STATIC_DIR.resolve())) or not resolved.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.send_bytes(resolved.read_bytes(), HTTPStatus.OK, content_type)

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/api/load":
                fields = parse_multipart(self.read_body(), self.headers.get("Content-Type", ""))
                uploaded = fields.get("file")
                if not uploaded or not uploaded.get("content"):
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "No ReqIF file uploaded")
                    return
                filename = str(uploaded.get("filename") or "document.reqif")
                content = uploaded["content"]
                job_id = start_job("Load ReqIF", lambda: create_session(filename, content))  # type: ignore[arg-type]
                self.send_json({"jobId": job_id})
            elif path.startswith("/api/session/") and path.endswith("/save"):
                session_id = path.split("/")[3]
                payload = json.loads(self.read_body().decode("utf-8"))
                updates = payload.get("objects", {})
                if not isinstance(updates, dict):
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid save payload")
                    return
                viewed_commit = str(payload.get("viewedCommit", ""))
                ui_state = payload.get("uiState")
                job_id = start_job("Save edits", lambda: save_session(session_id, updates, viewed_commit, ui_state))
                self.send_json({"jobId": job_id})
            elif path.startswith("/api/session/") and path.endswith("/export"):
                session_id = path.split("/")[3]
                job_id = start_job("Prepare export", lambda: create_export_artifact(session_id))
                self.send_json({"jobId": job_id})
            elif path.startswith("/api/session/") and path.endswith("/export-filtered"):
                session_id = path.split("/")[3]
                payload = json.loads(self.read_body().decode("utf-8"))
                object_ids = payload.get("objectIds", [])
                if not isinstance(object_ids, list) or not all(isinstance(object_id, str) for object_id in object_ids):
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid filtered export payload")
                    return
                job_id = start_job("Prepare filtered export", lambda: create_filtered_export_artifact(session_id, object_ids))
                self.send_json({"jobId": job_id})
            elif path == "/api/agent/analyze":
                payload = json.loads(self.read_body().decode("utf-8"))
                session_id = payload.get("sessionId")
                object_id = payload.get("objectId")
                selected_object = None
                if isinstance(session_id, str) and isinstance(object_id, str):
                    document_payload = load_payload(session_id)
                    objects = document_payload.get("objects", {})
                    if isinstance(objects, dict):
                        selected_object = objects.get(object_id)
                try:
                    self.send_json(
                        analyze_agent(
                            AgentRequest(
                                user_prompt=str(payload.get("prompt", "")),
                                session_id=session_id if isinstance(session_id, str) else None,
                                object_id=object_id if isinstance(object_id, str) else None,
                                selected_object=selected_object if isinstance(selected_object, dict) else None,
                            )
                        )
                    )
                except AgentBackendError as exc:
                    self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            elif path.startswith("/api/session/") and path.endswith("/object-text"):
                session_id = path.split("/")[3]
                payload = json.loads(self.read_body().decode("utf-8"))
                commit = str(payload.get("commit", ""))
                object_id = str(payload.get("objectId", ""))
                if not re.fullmatch(r"[a-f0-9]{7,40}", commit):
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid commit")
                    return
                if not object_id:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid object")
                    return
                try:
                    self.send_json(object_text_at_commit(session_id, commit, object_id))
                except ValueError as exc:
                    self.send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            elif path.startswith("/api/session/") and path.endswith("/commit-payload"):
                session_id = path.split("/")[3]
                payload = json.loads(self.read_body().decode("utf-8"))
                commit = str(payload.get("commit", ""))
                if not re.fullmatch(r"[a-f0-9]{7,40}", commit):
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid commit")
                    return
                try:
                    commit_payload = payload_at_commit(session_id, commit)
                    self.send_json({"commit": commit, "objects": commit_payload.get("objects", {})})
                except ValueError as exc:
                    self.send_error_json(HTTPStatus.NOT_FOUND, str(exc))
            elif path.startswith("/api/session/") and path.endswith("/checkout"):
                session_id = path.split("/")[3]
                payload = json.loads(self.read_body().decode("utf-8"))
                commit = str(payload.get("commit", ""))
                if not re.fullmatch(r"[a-f0-9]{7,40}", commit):
                    self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid commit")
                    return
                job_id = start_job(
                    "Load commit",
                    lambda: checkout_session_commit(session_id, commit),
                )
                self.send_json({"jobId": job_id})
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except ET.ParseError as exc:
            try:
                self.send_error_json(HTTPStatus.BAD_REQUEST, f"XHTML/XML parse error: {exc}")
            except ClientDisconnected:
                self.log_client_disconnect()
        except ClientDisconnected:
            self.log_client_disconnect()
        except Exception as exc:
            self.send_internal_error(str(exc))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Reqify web editor.")
    parser.add_argument("--debug", action="store_true", help="Print LLM prompts and git commands/responses to stderr.")
    args = parser.parse_args(argv)
    if args.debug:
        os.environ["REQIFY_DEBUG"] = "1"
    ensure_dirs()
    host = os.environ.get("REQIFY_HOST", "127.0.0.1")
    port = int(os.environ.get("REQIFY_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), ReqifyHandler)
    print(f"Reqify running at http://{host}:{port}")
    server.serve_forever()
