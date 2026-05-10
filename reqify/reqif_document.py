from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .xml_utils import (
    attribute_key,
    bool_attr,
    child_elements,
    collect_namespaces,
    descendant_text,
    direct_container,
    element_inner_xml,
    first_child,
    first_descendant,
    local_name,
    qualified_like,
    replace_xhtml_value,
    strip_markup,
)


class ReqifDocument:
    def __init__(self, xml_path: Path):
        self.xml_path = xml_path
        collect_namespaces(xml_path)
        self.tree = ET.parse(xml_path)
        self.root = self.tree.getroot()
        self.enum_options_by_datatype = self._enum_options_by_datatype()
        self.attribute_defs = self._attribute_definitions()
        self.definition_names = self._definition_names()
        self.spec_object_type_names = self._spec_object_type_names()
        self.spec_object_type_attribute_ids = self._spec_object_type_attribute_ids()
        self.spec_objects = self._spec_objects()

    def _definition_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for element in self.root.iter():
            if local_name(element.tag).startswith("ATTRIBUTE-DEFINITION"):
                identifier = element.get("IDENTIFIER")
                if identifier:
                    names[identifier] = element.get("LONG-NAME") or element.get("DESC") or identifier
        return names

    def _spec_object_type_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for element in self.root.iter():
            if local_name(element.tag) != "SPEC-OBJECT-TYPE":
                continue
            identifier = element.get("IDENTIFIER")
            if identifier:
                names[identifier] = element.get("LONG-NAME") or element.get("DESC") or identifier
        return names

    def _spec_object_type_attribute_ids(self) -> dict[str, list[str]]:
        attribute_ids: dict[str, list[str]] = {}
        for element in self.root.iter():
            if local_name(element.tag) != "SPEC-OBJECT-TYPE":
                continue
            type_id = element.get("IDENTIFIER")
            if not type_id:
                continue
            ids = []
            for nested in element.iter():
                if not local_name(nested.tag).startswith("ATTRIBUTE-DEFINITION"):
                    continue
                definition_id = nested.get("IDENTIFIER")
                if definition_id:
                    ids.append(definition_id)
            attribute_ids[type_id] = ids
        return attribute_ids

    def _enum_options_by_datatype(self) -> dict[str, list[dict[str, str]]]:
        datatypes: dict[str, list[dict[str, str]]] = {}
        for element in self.root.iter():
            if local_name(element.tag) != "DATATYPE-DEFINITION-ENUMERATION":
                continue
            datatype_id = element.get("IDENTIFIER")
            if not datatype_id:
                continue
            options = []
            for option in element.iter():
                if local_name(option.tag) != "ENUM-VALUE":
                    continue
                option_id = option.get("IDENTIFIER")
                if not option_id:
                    continue
                embedded = first_descendant(option, "EMBEDDED-VALUE")
                embedded_content = embedded.get("OTHER-CONTENT") if embedded is not None else ""
                label = option.get("LONG-NAME") or option.get("DESC") or embedded_content or option_id
                options.append({"id": option_id, "label": label})
            datatypes[datatype_id] = options
        return datatypes

    def _attribute_definitions(self) -> dict[str, dict[str, object]]:
        definitions: dict[str, dict[str, object]] = {}
        for element in self.root.iter():
            if not local_name(element.tag).startswith("ATTRIBUTE-DEFINITION"):
                continue
            definition_id = element.get("IDENTIFIER")
            if not definition_id:
                continue
            datatype_ref = ""
            for nested in element.iter():
                if local_name(nested.tag).startswith("DATATYPE-DEFINITION") and local_name(nested.tag).endswith("-REF"):
                    datatype_ref = (nested.text or "").strip()
                    break
            definitions[definition_id] = {
                "id": definition_id,
                "name": element.get("LONG-NAME") or element.get("DESC") or definition_id,
                "kind": local_name(element.tag).replace("ATTRIBUTE-DEFINITION-", "").lower(),
                "multiple": bool_attr(element, "MULTI-VALUED"),
                "datatype": datatype_ref,
                "options": self.enum_options_by_datatype.get(datatype_ref, []),
            }
        return definitions

    def _spec_objects(self) -> dict[str, ET.Element]:
        objects: dict[str, ET.Element] = {}
        for element in self.root.iter():
            if local_name(element.tag) == "SPEC-OBJECT":
                identifier = element.get("IDENTIFIER")
                if identifier:
                    objects[identifier] = element
        return objects

    def _spec_object_type_ref(self, spec_object: ET.Element) -> str:
        type_container = direct_container(spec_object, "TYPE")
        if type_container is None:
            return ""
        type_ref = descendant_text(type_container, "SPEC-OBJECT-TYPE-REF")
        return type_ref.strip() if type_ref else ""

    def _attribute_ref(self, value_element: ET.Element) -> str:
        for child in list(value_element):
            if local_name(child.tag).startswith("DEFINITION"):
                ref = first_descendant(child, "ATTRIBUTE-DEFINITION-XHTML-REF")
                if ref is not None and ref.text:
                    return ref.text.strip()
                for nested in child.iter():
                    if local_name(nested.tag).startswith("ATTRIBUTE-DEFINITION") and local_name(nested.tag).endswith("-REF"):
                        if nested.text:
                            return nested.text.strip()
        return value_element.get("IDENTIFIER") or uuid.uuid4().hex

    def _enum_value_refs(self, value_element: ET.Element) -> list[str]:
        values = first_child(value_element, "VALUES")
        if values is None:
            return []
        return [
            node.text.strip()
            for node in values.iter()
            if local_name(node.tag).endswith("-REF") and node.text and node.text.strip()
        ]

    def _value_payload(self, value_element: ET.Element) -> tuple[str, object]:
        kind = local_name(value_element.tag).replace("ATTRIBUTE-VALUE-", "").lower()
        if local_name(value_element.tag) == "ATTRIBUTE-VALUE-ENUMERATION":
            return "enumeration", self._enum_value_refs(value_element)
        if local_name(value_element.tag) == "ATTRIBUTE-VALUE-XHTML":
            return "xhtml", element_inner_xml(first_child(value_element, "THE-VALUE"))
        direct_value = value_element.get("THE-VALUE")
        if direct_value is not None:
            return kind, direct_value
        the_value = first_child(value_element, "THE-VALUE")
        if the_value is not None:
            if list(the_value):
                return kind, element_inner_xml(the_value)
            return kind, the_value.text or ""
        values = first_child(value_element, "VALUES")
        if values is not None:
            refs = [node.text.strip() for node in values.iter() if local_name(node.tag).endswith("-REF") and node.text]
            return kind, ", ".join(refs)
        return kind, ""

    def _object_attributes(self, spec_object: ET.Element) -> list[dict[str, object]]:
        values = direct_container(spec_object, "VALUES")
        attributes: list[dict[str, object]] = []
        if values is not None:
            for value_element in list(values):
                if not local_name(value_element.tag).startswith("ATTRIBUTE-VALUE"):
                    continue
                attr_id = self._attribute_ref(value_element)
                kind, value = self._value_payload(value_element)
                attributes.append(self._attribute_payload(attr_id, kind, value))
        existing_ids = {str(attribute["id"]) for attribute in attributes}
        type_id = self._spec_object_type_ref(spec_object)
        for attr_id in self.spec_object_type_attribute_ids.get(type_id, []):
            if attr_id in existing_ids or not self._is_verification_field_definition(attr_id):
                continue
            definition = self.attribute_defs.get(attr_id, {})
            kind = str(definition.get("kind", "string"))
            value: object = [] if kind == "enumeration" else ""
            attributes.append(self._attribute_payload(attr_id, kind, value, missing=True))
        return attributes

    def _attribute_payload(self, attr_id: str, kind: str, value: object, missing: bool = False) -> dict[str, object]:
        definition = self.attribute_defs.get(attr_id, {})
        options = definition.get("options", [])
        selected_labels = []
        if kind == "enumeration" and isinstance(value, list):
            labels_by_id = {str(option["id"]): str(option["label"]) for option in options if isinstance(option, dict)}
            selected_labels = [labels_by_id.get(str(ref), str(ref)) for ref in value]
        return {
            "id": attr_id,
            "name": self.definition_names.get(attr_id, attr_id),
            "type": kind,
            "value": value,
            "displayValue": ", ".join(selected_labels) if selected_labels else value,
            "multiple": bool(definition.get("multiple")) if kind == "enumeration" else False,
            "options": options if kind == "enumeration" else [],
            "editable": kind in {"xhtml", "string", "integer", "real", "date", "boolean", "enumeration"},
            "missing": missing,
        }

    def _is_verification_field_definition(self, attr_id: str) -> bool:
        name = self.definition_names.get(attr_id, attr_id)
        key = "".join(char for char in name.lower() if char.isalnum())
        return "verification" in key and (
            "criteria" in key
            or "criterion" in key
            or "method" in key
            or "measure" in key
            or "domain" in key
        )

    def _title_for(self, spec_id: str, attributes: list[dict[str, object]] | None = None) -> str:
        attrs = attributes if attributes is not None else self._object_attributes(self.spec_objects[spec_id])
        chapter_attr = next((attr for attr in attrs if attribute_key(attr).endswith("chaptername") and strip_markup(str(attr["value"]))), None)
        reqif_text_attr = next((attr for attr in attrs if attribute_key(attr) == "reqiftext" and strip_markup(str(attr["value"]))), None)
        xhtml_attr = next((attr for attr in attrs if attr["type"] == "xhtml" and strip_markup(str(attr["value"]))), None)
        text_attr = next((attr for attr in attrs if strip_markup(str(attr["value"]))), None)
        title = strip_markup(str((chapter_attr or reqif_text_attr or xhtml_attr or text_attr or {}).get("value", "")))
        return title[:90] if title else spec_id

    def _is_heading_object(self, spec_id: str) -> bool:
        spec_object = self.spec_objects.get(spec_id)
        if spec_object is None:
            return False
        type_id = self._spec_object_type_ref(spec_object)
        type_name = self.spec_object_type_names.get(type_id, "")
        return type_id.strip().lower() == "heading" or type_name.strip().lower() == "heading"

    def _hierarchy_node(self, hierarchy: ET.Element, number: str) -> dict[str, object] | None:
        object_ref = descendant_text(hierarchy, "SPEC-OBJECT-REF")
        children_container = direct_container(hierarchy, "CHILDREN")
        children = []
        if children_container is not None:
            heading_index = 0
            for child in child_elements(children_container, "SPEC-HIERARCHY"):
                child_ref = descendant_text(child, "SPEC-OBJECT-REF")
                child_number = ""
                if child_ref and self._is_heading_object(child_ref):
                    heading_index += 1
                    child_number = f"{number}.{heading_index}" if number else str(heading_index)
                node = self._hierarchy_node(child, child_number)
                if node:
                    children.append(node)
        if not object_ref:
            return None
        return {
            "id": hierarchy.get("IDENTIFIER") or object_ref,
            "objectId": object_ref,
            "number": number,
            "title": self._title_for(object_ref) if object_ref in self.spec_objects else object_ref,
            "children": children,
        }

    def _specifications(self) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        for specification in self.root.iter():
            if local_name(specification.tag) != "SPECIFICATION":
                continue
            children_container = direct_container(specification, "CHILDREN")
            children = []
            if children_container is not None:
                heading_index = 0
                for hierarchy in child_elements(children_container, "SPEC-HIERARCHY"):
                    object_ref = descendant_text(hierarchy, "SPEC-OBJECT-REF")
                    number = ""
                    if object_ref and self._is_heading_object(object_ref):
                        heading_index += 1
                        number = str(heading_index)
                    node = self._hierarchy_node(hierarchy, number)
                    if node:
                        children.append(node)
            specs.append(
                {
                    "id": specification.get("IDENTIFIER") or uuid.uuid4().hex,
                    "title": specification.get("LONG-NAME") or specification.get("DESC") or "Specification",
                    "children": children,
                }
            )
        return specs

    def _document_order(self, specs: list[dict[str, object]]) -> list[str]:
        order: list[str] = []

        def visit(nodes: list[dict[str, object]]) -> None:
            for node in nodes:
                object_id = str(node.get("objectId", ""))
                if object_id and object_id not in order:
                    order.append(object_id)
                visit(node.get("children", []))  # type: ignore[arg-type]

        for spec in specs:
            visit(spec.get("children", []))  # type: ignore[arg-type]
        for object_id in self.spec_objects:
            if object_id not in order:
                order.append(object_id)
        return order

    def _outline_numbers(self, specs: list[dict[str, object]]) -> dict[str, str]:
        numbers: dict[str, str] = {}

        def visit(nodes: list[dict[str, object]]) -> None:
            for node in nodes:
                object_id = str(node.get("objectId", ""))
                number = str(node.get("number", ""))
                if object_id and number and object_id not in numbers:
                    numbers[object_id] = number
                visit(node.get("children", []))  # type: ignore[arg-type]

        for spec in specs:
            visit(spec.get("children", []))  # type: ignore[arg-type]
        return numbers

    def as_payload(self) -> dict[str, object]:
        objects: dict[str, dict[str, object]] = {}
        for object_id, spec_object in self.spec_objects.items():
            attributes = self._object_attributes(spec_object)
            type_id = self._spec_object_type_ref(spec_object)
            objects[object_id] = {
                "id": object_id,
                "title": self._title_for(object_id, attributes),
                "objectTypeId": type_id,
                "objectTypeName": self.spec_object_type_names.get(type_id, type_id),
                "attributes": attributes,
            }
        specs = self._specifications()
        return {
            "specifications": specs,
            "objects": objects,
            "documentOrder": self._document_order(specs),
            "outlineNumbers": self._outline_numbers(specs),
        }

    def apply_updates(self, updates: dict[str, object]) -> None:
        last_change = reqif_timestamp()
        for object_id, attr_updates in updates.items():
            spec_object = self.spec_objects.get(object_id)
            if spec_object is None or not isinstance(attr_updates, dict):
                continue
            object_changed = False
            values = direct_container(spec_object, "VALUES")
            if values is None:
                values = self._create_values_container(spec_object)
            applied_ids = set()
            for value_element in list(values):
                if not local_name(value_element.tag).startswith("ATTRIBUTE-VALUE"):
                    continue
                attr_id = self._attribute_ref(value_element)
                update = attr_updates.get(attr_id)
                if not isinstance(update, dict) or "value" not in update:
                    continue
                raw_value = update["value"]
                if local_name(value_element.tag) == "ATTRIBUTE-VALUE-ENUMERATION":
                    self._apply_enum_update(value_element, raw_value)
                elif local_name(value_element.tag) == "ATTRIBUTE-VALUE-XHTML":
                    value = str(raw_value)
                    the_value = first_child(value_element, "THE-VALUE")
                    if the_value is None:
                        the_value = self._append_child(value_element, "THE-VALUE", "\n              ")
                    replace_xhtml_value(the_value, value)
                    self._format_xhtml_value(the_value)
                elif "THE-VALUE" in value_element.attrib:
                    value_element.set("THE-VALUE", str(raw_value))
                else:
                    value = str(raw_value)
                    the_value = first_child(value_element, "THE-VALUE")
                    if the_value is not None:
                        the_value.clear()
                        the_value.text = value
                applied_ids.add(attr_id)
                object_changed = True
            type_id = self._spec_object_type_ref(spec_object)
            allowed_ids = set(self.spec_object_type_attribute_ids.get(type_id, []))
            for attr_id, update in attr_updates.items():
                if attr_id in applied_ids or attr_id not in allowed_ids or not isinstance(update, dict) or "value" not in update:
                    continue
                definition = self.attribute_defs.get(str(attr_id), {})
                kind = str(definition.get("kind", ""))
                if kind not in {"xhtml", "string", "integer", "real", "date", "boolean", "enumeration"}:
                    continue
                value_element = self._create_value_element(values, str(attr_id), kind)
                self._apply_value_update(value_element, update["value"])
                object_changed = True
            if object_changed:
                spec_object.set("LAST-CHANGE", last_change)

    def _create_value_element(self, values: ET.Element, attr_id: str, kind: str) -> ET.Element:
        children = list(values)
        if children:
            child_indent = self._child_indent(values, "\n            ")
            closing_indent = self._closing_indent(values, child_indent)
            children[-1].tail = child_indent
        else:
            closing_indent = values.text if self._is_indent(values.text) else "\n          "
            child_indent = self._deeper_indent(closing_indent)
            values.text = child_indent
        value_element = ET.SubElement(values, qualified_like(values, f"ATTRIBUTE-VALUE-{kind.upper()}"))
        value_element.text = self._deeper_indent(child_indent)
        value_element.tail = closing_indent
        definition = ET.SubElement(value_element, qualified_like(values, "DEFINITION"))
        definition.text = self._deeper_indent(value_element.text)
        definition.tail = value_element.text
        ref = ET.SubElement(definition, qualified_like(values, f"ATTRIBUTE-DEFINITION-{kind.upper()}-REF"))
        ref.text = attr_id
        ref.tail = definition.tail
        return value_element

    def _create_values_container(self, spec_object: ET.Element) -> ET.Element:
        child_indent = self._child_indent(spec_object, "\n          ")
        closing_indent = self._closing_indent(spec_object, child_indent)
        children = list(spec_object)
        values = ET.Element(qualified_like(spec_object, "VALUES"))
        values.text = self._deeper_indent(child_indent)
        values.tail = child_indent if children else closing_indent
        if spec_object.text is None:
            spec_object.text = child_indent
        spec_object.insert(0, values)
        return values

    def _append_child(self, parent: ET.Element, tag_name: str, fallback_indent: str) -> ET.Element:
        children = list(parent)
        if children:
            child_indent = self._child_indent(parent, fallback_indent)
            closing_indent = self._closing_indent(parent, child_indent)
            children[-1].tail = child_indent
        else:
            closing_indent = parent.text if self._is_indent(parent.text) else self._less_indented(fallback_indent)
            child_indent = self._deeper_indent(closing_indent)
            parent.text = child_indent
        child = ET.SubElement(parent, qualified_like(parent, tag_name))
        child.tail = closing_indent
        return child

    def _child_indent(self, parent: ET.Element, fallback: str) -> str:
        if self._is_indent(parent.text):
            return parent.text or fallback
        for child in list(parent):
            if self._is_indent(child.tail):
                return child.tail or fallback
        return fallback

    def _closing_indent(self, parent: ET.Element, child_indent: str) -> str:
        children = list(parent)
        if children and self._is_indent(children[-1].tail) and children[-1].tail != child_indent:
            return children[-1].tail or self._less_indented(child_indent)
        return self._less_indented(child_indent)

    @staticmethod
    def _is_indent(value: str | None) -> bool:
        return value is not None and "\n" in value and not value.strip()

    @staticmethod
    def _deeper_indent(indent: str | None) -> str:
        return f"{indent or ''}  "

    @staticmethod
    def _less_indented(indent: str) -> str:
        newline, _, spaces = indent.rpartition("\n")
        if not newline and not indent.startswith("\n"):
            return indent
        return f"{newline}\n{spaces[:-2] if len(spaces) >= 2 else spaces}"

    def _apply_value_update(self, value_element: ET.Element, raw_value: object) -> None:
        if local_name(value_element.tag) == "ATTRIBUTE-VALUE-ENUMERATION":
            self._apply_enum_update(value_element, raw_value)
        elif local_name(value_element.tag) == "ATTRIBUTE-VALUE-XHTML":
            the_value = first_child(value_element, "THE-VALUE")
            if the_value is None:
                the_value = self._append_child(value_element, "THE-VALUE", "\n              ")
            replace_xhtml_value(the_value, str(raw_value))
            self._format_xhtml_value(the_value)
        else:
            value_element.set("THE-VALUE", str(raw_value))

    def _apply_enum_update(self, value_element: ET.Element, raw_value: object) -> None:
        if isinstance(raw_value, list):
            selected_refs = [str(ref) for ref in raw_value if str(ref)]
        elif raw_value:
            selected_refs = [str(raw_value)]
        else:
            selected_refs = []
        values_element = first_child(value_element, "VALUES")
        if values_element is None:
            values_element = self._append_child(value_element, "VALUES", "\n              ")
        ref_tag = qualified_like(value_element, "ENUM-VALUE-REF")
        for child in values_element.iter():
            if local_name(child.tag).endswith("-REF"):
                ref_tag = child.tag
                break
        for child in list(values_element):
            values_element.remove(child)
        child_indent = self._child_indent(values_element, "\n                ")
        closing_indent = self._closing_indent(values_element, child_indent)
        values_element.text = child_indent if selected_refs else None
        for selected_ref in selected_refs:
            ref_element = ET.SubElement(values_element, ref_tag)
            ref_element.text = selected_ref
            ref_element.tail = child_indent
        if selected_refs:
            list(values_element)[-1].tail = closing_indent

    def _format_xhtml_value(self, the_value: ET.Element) -> None:
        children = list(the_value)
        if not children or (the_value.text and the_value.text.strip()):
            return
        child_indent = self._child_indent(the_value, "\n                ")
        closing_indent = self._closing_indent(the_value, child_indent)
        the_value.text = child_indent
        for child in children[:-1]:
            if not self._is_indent(child.tail):
                child.tail = child_indent
        if not self._is_indent(children[-1].tail):
            children[-1].tail = closing_indent

    def write(self) -> None:
        self.tree.write(self.xml_path, encoding="utf-8", xml_declaration=True, short_empty_elements=True)


def reqif_timestamp() -> str:
    value = datetime.now().astimezone()
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}{value:%z}"[:-2] + ":" + f"{value:%z}"[-2:]
