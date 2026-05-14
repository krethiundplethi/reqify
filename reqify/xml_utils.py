from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from xml.etree import ElementTree as ET


XHTML_NS = "http://www.w3.org/1999/xhtml"
VOID_TAGS = ("br", "hr", "img", "input", "meta", "link")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text


def attribute_key(attribute: dict[str, object]) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(attribute.get("name") or attribute.get("id") or "").lower())


def bool_attr(element: ET.Element, name: str) -> bool:
    return str(element.get(name, "")).lower() in {"1", "true", "yes"}


def qualified_like(element: ET.Element, name: str) -> str:
    if element.tag.startswith("{"):
        namespace = element.tag[1:].split("}", 1)[0]
        return f"{{{namespace}}}{name}"
    return name


def element_inner_xml(element: ET.Element | None, convert_objects: bool = True) -> str:
    if element is None:
        return ""
    parts: list[str] = []
    if element.text:
        parts.append(html.escape(element.text))
    for child in list(element):
        parts.append(browser_html_fragment(child, convert_objects))
    return "".join(parts)


def cleaned_element_inner_xml(element: ET.Element | None, convert_objects: bool = True) -> str:
    return strip_structural_indent(element_inner_xml(element, convert_objects))


def strip_structural_indent(value: str) -> str:
    if not value:
        return ""
    lines = value.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    indents = [len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()]
    common_indent = min(indents) if indents else 0
    if common_indent:
        lines = [line[common_indent:] if line.strip() else "" for line in lines]
    return "\n".join(lines)


def browser_html_fragment(element: ET.Element, convert_objects: bool = True) -> str:
    tag = local_name(element.tag)
    if convert_objects and tag.lower() == "object" and element.get("data"):
        return attachment_object_fragment(element)
    attrs = "".join(f' {local_name(name)}="{html.escape(value, quote=True)}"' for name, value in element.attrib.items())
    parts: list[str] = []
    if tag.lower() in VOID_TAGS:
        parts.append(f"<{tag}{attrs}>")
        if element.text:
            parts.append(html.escape(element.text))
        for child in list(element):
            parts.append(browser_html_fragment(child, convert_objects))
    else:
        parts.append(f"<{tag}{attrs}>")
        if element.text:
            parts.append(html.escape(element.text))
        for child in list(element):
            parts.append(browser_html_fragment(child, convert_objects))
        parts.append(f"</{tag}>")
    if element.tail:
        parts.append(html.escape(element.tail))
    return "".join(parts)


def attachment_object_fragment(element: ET.Element) -> str:
    data = element.get("data") or ""
    name = element.get("name") or ""
    content_type = element.get("type") or ""
    fallback = element_inner_xml(element, convert_objects=False)
    label = name or basename(data) or strip_markup(fallback) or "Attachment"
    fallback_encoded = base64.b64encode(fallback.encode("utf-8")).decode("ascii")
    attrs = {
        "data-reqif-xhtml-object": "1",
        "data-reqif-object-data": data,
        "data-reqif-object-name": name,
        "data-reqif-object-type": content_type,
        "data-reqif-object-fallback": fallback_encoded,
        "contenteditable": "false",
        "class": "reqif-attachment",
    }
    attr_text = "".join(f' {key}="{html.escape(value, quote=True)}"' for key, value in attrs.items())
    link_title = f"Download {label}"
    return (
        f"<span{attr_text}>"
        f'<a data-reqif-object-link="1" href="#" title="{html.escape(link_title, quote=True)}">'
        f"{html.escape(label)}"
        "</a></span>"
        + (html.escape(element.tail) if element.tail else "")
    )


def basename(path: str) -> str:
    return re.split(r"[\\/]", path)[-1] if path else ""


def collect_namespaces(xml_path: Path) -> None:
    try:
        for _, ns in ET.iterparse(xml_path, events=("start-ns",)):
            prefix, uri = ns
            if re.fullmatch(r"ns\d+", prefix or ""):
                continue
            ET.register_namespace(prefix or "", uri)
    except ET.ParseError:
        return


def first_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in list(element):
        if local_name(child.tag) == name:
            return child
    return None


def first_descendant(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if child is not element and local_name(child.tag) == name:
            return child
    return None


def descendant_text(element: ET.Element, name: str) -> str:
    found = first_descendant(element, name)
    return (found.text or "").strip() if found is not None else ""


def child_elements(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if local_name(child.tag) == name]


def direct_container(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        if local_name(child.tag) == name:
            return child
    return None


def normalize_fragment(fragment: str) -> str:
    fragment = restore_attachment_object_markers(fragment)
    fragment = fragment.replace("&nbsp;", "&#160;")
    fragment = re.sub(r'\s+xmlns(?::[A-Za-z_][\w.-]*)?="[^"]*"', "", fragment)
    fragment = re.sub(r"</?(?:xhtml|html):", lambda match: match.group(0).replace("xhtml:", "").replace("html:", ""), fragment)
    fragment = re.sub(r"<br\s*/\s*>", "<br />", fragment, flags=re.IGNORECASE)
    for tag in VOID_TAGS:
        fragment = re.sub(rf"<{tag}([^>/]*?)>", rf"<{tag}\1 />", fragment, flags=re.IGNORECASE)
        fragment = re.sub(rf"</{tag}>", "", fragment, flags=re.IGNORECASE)
    return fragment


def restore_attachment_object_markers(fragment: str) -> str:
    pattern = re.compile(
        r"<span\b(?=[^>]*\bdata-reqif-xhtml-object\s*=\s*['\"]1['\"])(?P<attrs>[^>]*)>(?P<body>.*?)</span>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(lambda match: restored_attachment_object(match.group("attrs"), match.group("body")), fragment)


def restored_attachment_object(attrs_text: str, body: str) -> str:
    attrs = html_attrs(attrs_text)
    data = attrs.get("data-reqif-object-data", "")
    name = attrs.get("data-reqif-object-name", "")
    content_type = attrs.get("data-reqif-object-type", "")
    fallback = decode_base64_text(attrs.get("data-reqif-object-fallback", ""))
    if not fallback:
        fallback = html.escape(strip_markup(body) or name or basename(data) or "Attachment")
    object_attrs = {"data": data, "name": name, "type": content_type}
    attr_text = "".join(f' {key}="{html.escape(value, quote=True)}"' for key, value in object_attrs.items() if value)
    return f"<object{attr_text}>{fallback}</object>"


def html_attrs(attrs_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z_:][\w:.-]*)\s*=\s*(\"([^\"]*)\"|'([^']*)')", attrs_text):
        attrs[match.group(1).lower()] = html.unescape(match.group(3) if match.group(3) is not None else match.group(4) or "")
    return attrs


def decode_base64_text(value: str) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
    except Exception:
        return ""


def parse_xhtml_fragment(fragment: str) -> ET.Element:
    wrapped = f'<div xmlns="{XHTML_NS}">{normalize_fragment(fragment)}</div>'
    return ET.fromstring(wrapped)


def xhtml_value_div(fragment: str) -> ET.Element | None:
    normalized = normalize_fragment(fragment)
    if not normalized.strip():
        return None
    wrapper = ET.fromstring(f'<div xmlns="{XHTML_NS}">{normalized}</div>')
    children = list(wrapper)
    if not (wrapper.text or "").strip() and len(children) == 1 and local_name(children[0].tag) == "div" and not (children[0].tail or "").strip():
        child = children[0]
        wrapper.remove(child)
        child.tail = None
        return child
    return wrapper


def replace_xhtml_value(the_value: ET.Element, fragment: str) -> None:
    tail = the_value.tail
    the_value.clear()
    the_value.tail = tail
    div = xhtml_value_div(fragment)
    if div is not None:
        the_value.append(div)
