from __future__ import annotations

import html
import io
import os
import posixpath
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

from .session_store import load_attachment
from .xml_utils import html_attrs, local_name


def excel_attachment_prompt_context(session_id: str | None, selected_object: dict[str, object] | None) -> str:
    if not session_id:
        print_excel_debug_context("Excel attachment preview skipped: no session id.")
        return ""
    if not selected_object:
        print_excel_debug_context("Excel attachment preview skipped: no selected object.")
        return ""
    paths = excel_attachment_paths(selected_object)
    if not paths:
        print_excel_debug_context("Excel attachment preview skipped: no Excel attachments found in selected object.")
        return ""
    lines = ["Attachment table previews:"]
    for rel_path in paths:
        try:
            name, body, _ = load_attachment(session_id, rel_path)
            table = excel_attachment_table(name, body)
        except Exception as exc:
            print_excel_debug(rel_path, exc)
            continue
        if table:
            lines.append(f"{name} ({rel_path}), first sheet, max 10 rows x 10 columns:")
            lines.extend(markdown_table(table))
        else:
            print_excel_debug_context(f"Excel attachment preview skipped for {rel_path}: no table content extracted.")
    if len(lines) <= 1:
        print_excel_debug_context("Excel attachment preview skipped: all Excel attachments produced empty previews.")
        return ""
    return "\n".join(lines)


def excel_attachment_paths(selected_object: dict[str, object]) -> list[str]:
    paths: list[str] = []
    attributes = selected_object.get("attributes")
    if not isinstance(attributes, list):
        return paths
    for attribute in attributes:
        if not isinstance(attribute, dict) or attribute.get("type") != "xhtml":
            continue
        value = str(attribute.get("value") or "")
        for path in attachment_paths_in_xhtml(value):
            if is_excel_path(path) and path not in paths:
                paths.append(path)
    return paths


def attachment_paths_in_xhtml(value: str) -> list[str]:
    paths: list[str] = []
    marker_pattern = re.compile(
        r"<span\b(?=[^>]*\bdata-reqif-xhtml-object\s*=\s*['\"]1['\"])(?P<attrs>[^>]*)>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in marker_pattern.finditer(value):
        path = html_attrs(match.group("attrs")).get("data-reqif-object-data", "").strip()
        if path:
            paths.append(path)
    object_pattern = re.compile(r"<(?:xhtml:)?object\b(?P<attrs>[^>]*)>", flags=re.IGNORECASE | re.DOTALL)
    for match in object_pattern.finditer(value):
        path = html_attrs(match.group("attrs")).get("data", "").strip()
        if path:
            paths.append(path)
    return paths


def is_excel_path(path: str) -> bool:
    return path.lower().split("?", 1)[0].split("#", 1)[0].endswith((".xls", ".xlsx"))


def excel_attachment_table(name: str, body: bytes) -> list[list[str]]:
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix == "xlsx":
        return xlsx_first_sheet_table(body)
    if suffix == "xls":
        return xls_first_sheet_table(body)
    return []


def xlsx_first_sheet_table(body: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        workbook_path = "xl/workbook.xml"
        rels_path = "xl/_rels/workbook.xml.rels"
        sheet_path = first_xlsx_sheet_path(archive, workbook_path, rels_path)
        shared_strings = xlsx_shared_strings(archive)
        root = ET.fromstring(archive.read(sheet_path))
    rows: list[list[str]] = []
    for row in root.iter():
        if local_name(row.tag) != "row":
            continue
        values = [""] * 10
        for cell in list(row):
            if local_name(cell.tag) != "c":
                continue
            column = xlsx_column_index(cell.get("r", ""))
            if column < 0 or column >= 10:
                continue
            values[column] = xlsx_cell_text(cell, shared_strings)
        rows.append(values)
        if len(rows) >= 10:
            break
    return trim_table(rows)


def first_xlsx_sheet_path(archive: zipfile.ZipFile, workbook_path: str, rels_path: str) -> str:
    workbook = ET.fromstring(archive.read(workbook_path))
    first_rel_id = ""
    for node in workbook.iter():
        if local_name(node.tag) == "sheet":
            first_rel_id = node.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
            break
    rels = ET.fromstring(archive.read(rels_path))
    for rel in rels:
        if rel.get("Id") == first_rel_id:
            target = rel.get("Target", "")
            if target.startswith("/"):
                return target.lstrip("/")
            return posixpath.normpath(posixpath.join(posixpath.dirname(workbook_path), target))
    return "xl/worksheets/sheet1.xml"


def xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for item in root:
        if local_name(item.tag) != "si":
            continue
        parts = [node.text or "" for node in item.iter() if local_name(node.tag) == "t"]
        values.append("".join(parts))
    return values


def xlsx_column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref.upper() if "A" <= char <= "Z")
    if not letters:
        return -1
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def xlsx_cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.get("t", "")
    if cell_type == "inlineStr":
        return normalize_cell_text("".join(node.text or "" for node in cell.iter() if local_name(node.tag) == "t"))
    value_node = next((node for node in cell if local_name(node.tag) == "v"), None)
    value = value_node.text if value_node is not None and value_node.text else ""
    if cell_type == "s":
        try:
            return normalize_cell_text(shared_strings[int(value)])
        except (ValueError, IndexError):
            return ""
    return normalize_cell_text(value)


def xls_first_sheet_table(body: bytes) -> list[list[str]]:
    try:
        import xlrd  # type: ignore[import-not-found]
    except ImportError as exc:
        print_excel_debug_context(f"Excel attachment preview skipped for .xls: xlrd is not installed ({exc}).")
        return []
    try:
        workbook = xlrd.open_workbook(file_contents=body)
    except Exception as exc:
        print_excel_debug_context(f"Excel attachment preview failed for .xls workbook: {type(exc).__name__}: {exc}")
        return []
    if workbook.nsheets < 1:
        print_excel_debug_context("Excel attachment preview skipped for .xls workbook: no sheets found.")
        return []
    sheet = workbook.sheet_by_index(0)
    rows = []
    for row_index in range(min(sheet.nrows, 10)):
        rows.append([normalize_cell_text(sheet.cell_value(row_index, col_index)) for col_index in range(min(sheet.ncols, 10))])
    return trim_table(rows)


def normalize_cell_text(value: object) -> str:
    text = html.unescape(str(value)).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")[:10]]
    return " ".join(line for line in lines if line)[:140]


def trim_table(rows: list[list[str]]) -> list[list[str]]:
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    max_columns = 0
    for row in rows:
        for index, cell in enumerate(row):
            if cell.strip():
                max_columns = max(max_columns, index + 1)
    return [row[:max_columns] for row in rows] if max_columns else []


def markdown_table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = [cell or f"Column {index + 1}" for index, cell in enumerate(padded[0])]
    lines = [
        "| " + " | ".join(escape_markdown_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(escape_markdown_cell(cell) for cell in row) + " |")
    if len(padded) == 1:
        lines.append("| " + " | ".join("" for _ in header) + " |")
    return lines


def escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def print_excel_debug(path: str, exc: Exception) -> None:
    print_excel_debug_context(f"Excel attachment preview failed for {path}: {type(exc).__name__}: {exc}")


def print_excel_debug_context(message: str) -> None:
    if os.environ.get("REQIFY_DEBUG", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    print(message, file=sys.stderr)
