from __future__ import annotations

import json
import re


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def parse_multipart(body: bytes, content_type: str) -> dict[str, dict[str, object]]:
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not match:
        raise ValueError("Missing multipart boundary")
    boundary = ("--" + match.group(1)).encode("utf-8")
    fields: dict[str, dict[str, object]] = {}
    for part in body.split(boundary):
        if not part or part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        headers_raw, _, content = part.partition(b"\r\n\r\n")
        headers = headers_raw.decode("iso-8859-1")
        disposition = next((line for line in headers.split("\r\n") if line.lower().startswith("content-disposition:")), "")
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        name = name_match.group(1)
        fields[name] = {
            "filename": filename_match.group(1) if filename_match else "",
            "content": content,
        }
    return fields

