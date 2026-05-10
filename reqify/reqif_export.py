from __future__ import annotations

import copy
import io
import re
import tempfile
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .reqif_document import ReqifDocument
from .xml_utils import child_elements, descendant_text, direct_container, first_descendant, local_name, qualified_like


def edited_export_name(original_name: str, fallback_suffix: str) -> str:
    path = Path(original_name)
    suffix = path.suffix or fallback_suffix
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    return f"{stem}-edited{suffix}"


def filtered_export_name(original_name: str, fallback_suffix: str) -> str:
    path = Path(original_name)
    suffix = path.suffix or fallback_suffix
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    return f"{stem}-filtered{suffix}"


def export_session(
    original_name: str,
    is_zip: bool,
    repo: Path,
    document_path: Path,
    session_dir: Path,
    ui_state_rel: str,
) -> tuple[str, bytes, str]:
    if is_zip:
        export_name = edited_export_name(original_name, ".reqifz")
        export_path = session_dir / export_name
        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in repo.rglob("*"):
                if path.is_file() and ".git" not in path.parts and path.relative_to(repo).as_posix() != ui_state_rel:
                    archive.write(path, path.relative_to(repo))
        return export_name, export_path.read_bytes(), "application/zip"
    export_name = edited_export_name(original_name, ".reqif")
    return export_name, document_path.read_bytes(), "application/xml"


def export_filtered_session(
    original_name: str,
    is_zip: bool,
    repo: Path,
    document_path: Path,
    document_rel: Path,
    object_ids: list[str],
) -> tuple[str, bytes, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        temp_document = temp_root / document_rel
        temp_document.parent.mkdir(parents=True, exist_ok=True)
        temp_document.write_bytes(document_path.read_bytes())
        document = ReqifDocument(temp_document)
        referenced_paths = filter_document_to_objects(document, object_ids)
        document.write()
        if is_zip:
            export_name = filtered_export_name(original_name, ".reqifz")
            archive_buffer = io.BytesIO()
            with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(temp_document, document_rel.as_posix())
                for rel_path in sorted(referenced_paths):
                    source_path, archive_name = referenced_repo_file(repo, document_rel.parent, rel_path)
                    if source_path is not None and archive_name:
                        archive.write(source_path, archive_name)
            return export_name, archive_buffer.getvalue(), "application/zip"
        export_name = filtered_export_name(original_name, ".reqif")
        return export_name, temp_document.read_bytes(), "application/xml"


def filter_document_to_objects(document: ReqifDocument, object_ids: list[str]) -> set[str]:
    selected_ids = []
    for object_id in object_ids:
        if object_id not in selected_ids:
            selected_ids.append(object_id)
    if not selected_ids:
        raise ValueError("No objects selected for filtered export.")
    missing_ids = [object_id for object_id in selected_ids if object_id not in document.spec_objects]
    if missing_ids:
        raise ValueError(f"Unknown object id for filtered export: {missing_ids[0]}")

    content = first_descendant(document.root, "REQ-IF-CONTENT")
    if content is None:
        raise ValueError("ReqIF content not found.")

    selected_set = set(selected_ids)
    kept_objects = [document.spec_objects[object_id] for object_id in selected_ids]
    used_object_type_ids = {document._spec_object_type_ref(spec_object) for spec_object in kept_objects}
    used_attribute_ids: set[str] = set()
    for spec_object in kept_objects:
        values = direct_container(spec_object, "VALUES")
        if values is None:
            continue
        for value_element in list(values):
            if not local_name(value_element.tag).startswith("ATTRIBUTE-VALUE"):
                continue
            used_attribute_ids.add(document._attribute_ref(value_element))

    filter_spec_objects(content, selected_ids)
    filter_specifications(document, content, selected_ids)

    attribute_definitions = elements_by_identifier(document.root, "ATTRIBUTE-DEFINITION")
    used_attribute_ids.update(attribute_ids_for_type_elements(document.root, "SPEC-OBJECT-TYPE", used_object_type_ids))
    used_specification_type_ids = specification_type_ids(content)
    used_attribute_ids.update(attribute_ids_for_type_elements(document.root, "SPECIFICATION-TYPE", used_specification_type_ids))
    used_datatype_ids = {
        datatype_ref
        for attr_id in used_attribute_ids
        for datatype_ref in [datatype_ref_for_attribute(attribute_definitions.get(attr_id))]
        if datatype_ref
    }

    filter_spec_types(content, used_object_type_ids)
    filter_datatypes(content, used_datatype_ids)
    drop_container(content, "SPEC-RELATIONS")
    drop_container(content, "SPEC-RELATION-GROUPS")
    document.spec_objects = {object_id: document.spec_objects[object_id] for object_id in selected_ids}
    return referenced_file_paths(document, selected_set)


def referenced_file_paths(document: ReqifDocument, object_ids: set[str]) -> set[str]:
    references: set[str] = set()
    for object_id in object_ids:
        spec_object = document.spec_objects.get(object_id)
        if spec_object is None:
            continue
        for element in spec_object.iter():
            for attr_name, value in element.attrib.items():
                if local_name(attr_name) not in {"data", "src"}:
                    continue
                path = str(value).strip()
                if not path or re.match(r"^[a-z][a-z0-9+.-]*:", path, flags=re.IGNORECASE) or path.startswith(("/", "#")):
                    continue
                references.add(path.split("#", 1)[0].split("?", 1)[0])
    return references


def filter_spec_objects(content: ET.Element, selected_ids: list[str]) -> None:
    container = direct_container(content, "SPEC-OBJECTS")
    if container is None:
        return
    by_id = {child.get("IDENTIFIER"): child for child in list(container) if local_name(child.tag) == "SPEC-OBJECT"}
    for child in list(container):
        if local_name(child.tag) == "SPEC-OBJECT":
            container.remove(child)
    for object_id in selected_ids:
        element = by_id.get(object_id)
        if element is not None:
            container.append(element)


def filter_specifications(document: ReqifDocument, content: ET.Element, selected_ids: list[str]) -> None:
    container = direct_container(content, "SPECIFICATIONS")
    if container is None:
        return
    specifications = [child for child in list(container) if local_name(child.tag) == "SPECIFICATION"]
    if not specifications:
        return
    source_spec = specifications[0]
    source_hierarchies = hierarchies_by_object_ref(document.root)
    flat_spec = copy.deepcopy(source_spec)
    for child in list(flat_spec):
        if local_name(child.tag) == "CHILDREN":
            flat_spec.remove(child)
    children = ET.Element(qualified_like(flat_spec, "CHILDREN"))
    for object_id in selected_ids:
        source_hierarchy = source_hierarchies.get(object_id)
        if source_hierarchy is not None:
            hierarchy = copy.deepcopy(source_hierarchy)
            for child in list(hierarchy):
                if local_name(child.tag) in {"OBJECT", "CHILDREN"}:
                    hierarchy.remove(child)
        else:
            hierarchy = ET.Element(qualified_like(children, "SPEC-HIERARCHY"), {"IDENTIFIER": uuid.uuid4().hex})
        object_node = ET.SubElement(hierarchy, qualified_like(hierarchy, "OBJECT"))
        ref_node = ET.SubElement(object_node, qualified_like(object_node, "SPEC-OBJECT-REF"))
        ref_node.text = object_id
        children.append(hierarchy)
    flat_spec.append(children)
    for child in list(container):
        if local_name(child.tag) == "SPECIFICATION":
            container.remove(child)
    container.append(flat_spec)


def filter_spec_types(content: ET.Element, object_type_ids: set[str]) -> None:
    container = direct_container(content, "SPEC-TYPES")
    if container is None:
        return
    keep_specification_type_ids = specification_type_ids(content)
    for child in list(container):
        child_name = local_name(child.tag)
        identifier = child.get("IDENTIFIER") or ""
        keep = False
        if child_name == "SPEC-OBJECT-TYPE":
            keep = identifier in object_type_ids
        elif child_name == "SPECIFICATION-TYPE":
            keep = identifier in keep_specification_type_ids
        if not keep:
            container.remove(child)


def filter_datatypes(content: ET.Element, datatype_ids: set[str]) -> None:
    container = direct_container(content, "DATATYPES")
    if container is None:
        return
    for child in list(container):
        if not local_name(child.tag).startswith("DATATYPE-DEFINITION"):
            continue
        if child.get("IDENTIFIER") not in datatype_ids:
            container.remove(child)


def drop_container(content: ET.Element, name: str) -> None:
    container = direct_container(content, name)
    if container is not None:
        content.remove(container)


def elements_by_identifier(root: ET.Element, local_prefix: str) -> dict[str, ET.Element]:
    return {
        identifier: element
        for element in root.iter()
        for identifier in [element.get("IDENTIFIER")]
        if identifier and local_name(element.tag).startswith(local_prefix)
    }


def datatype_ref_for_attribute(attribute_definition: ET.Element | None) -> str:
    if attribute_definition is None:
        return ""
    for node in attribute_definition.iter():
        if local_name(node.tag).startswith("DATATYPE-DEFINITION") and local_name(node.tag).endswith("-REF") and node.text:
            return node.text.strip()
    return ""


def attribute_ids_for_type_elements(root: ET.Element, type_name: str, type_ids: set[str]) -> set[str]:
    attribute_ids: set[str] = set()
    for element in root.iter():
        if local_name(element.tag) != type_name or element.get("IDENTIFIER") not in type_ids:
            continue
        for nested in element.iter():
            if local_name(nested.tag).startswith("ATTRIBUTE-DEFINITION"):
                identifier = nested.get("IDENTIFIER")
                if identifier:
                    attribute_ids.add(identifier)
    return attribute_ids


def specification_type_ids(content: ET.Element) -> set[str]:
    specifications = direct_container(content, "SPECIFICATIONS")
    if specifications is None:
        return set()
    return {
        (node.text or "").strip()
        for specification in child_elements(specifications, "SPECIFICATION")
        for node in specification.iter()
        if local_name(node.tag) == "SPECIFICATION-TYPE-REF" and node.text and node.text.strip()
    }


def hierarchies_by_object_ref(root: ET.Element) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for hierarchy in root.iter():
        if local_name(hierarchy.tag) != "SPEC-HIERARCHY":
            continue
        object_ref = descendant_text(hierarchy, "SPEC-OBJECT-REF")
        if object_ref and object_ref not in result:
            result[object_ref] = hierarchy
    return result


def safe_repo_file(repo: Path, rel_path: str) -> Path | None:
    relative = Path(rel_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    source_path = (repo / relative).resolve()
    repo_resolved = repo.resolve()
    if source_path != repo_resolved and repo_resolved not in source_path.parents:
        return None
    return source_path if source_path.is_file() else None


def referenced_repo_file(repo: Path, document_parent: Path, rel_path: str) -> tuple[Path | None, str]:
    candidates = [Path(rel_path)]
    if str(document_parent) not in {"", "."}:
        candidates.append(document_parent / rel_path)
    for candidate in candidates:
        source_path = safe_repo_file(repo, candidate.as_posix())
        if source_path is not None:
            return source_path, candidate.as_posix()
    return None, ""
