from __future__ import annotations

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


def element_inner_xml(element: ET.Element | None) -> str:
    if element is None:
        return ""
    parts: list[str] = []
    if element.text:
        parts.append(html.escape(element.text))
    for child in list(element):
        parts.append(browser_html_fragment(child))
    return "".join(parts)


def browser_html_fragment(element: ET.Element) -> str:
    tag = local_name(element.tag)
    attrs = "".join(f' {local_name(name)}="{html.escape(value, quote=True)}"' for name, value in element.attrib.items())
    parts: list[str] = []
    if tag.lower() in VOID_TAGS and not list(element) and not element.text:
        parts.append(f"<{tag}{attrs}>")
    else:
        parts.append(f"<{tag}{attrs}>")
        if element.text:
            parts.append(html.escape(element.text))
        for child in list(element):
            parts.append(browser_html_fragment(child))
        parts.append(f"</{tag}>")
    if element.tail:
        parts.append(html.escape(element.tail))
    return "".join(parts)


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
    fragment = fragment.replace("&nbsp;", "&#160;")
    fragment = re.sub(r'\s+xmlns(?::[A-Za-z_][\w.-]*)?="[^"]*"', "", fragment)
    fragment = re.sub(r"</?(?:xhtml|html):", lambda match: match.group(0).replace("xhtml:", "").replace("html:", ""), fragment)
    fragment = re.sub(r"<br\s*/\s*>", "<br />", fragment, flags=re.IGNORECASE)
    for tag in VOID_TAGS:
        fragment = re.sub(rf"<{tag}([^>/]*?)>", rf"<{tag}\1 />", fragment, flags=re.IGNORECASE)
        fragment = re.sub(rf"</{tag}>", "", fragment, flags=re.IGNORECASE)
    return fragment


def parse_xhtml_fragment(fragment: str) -> ET.Element:
    wrapped = f'<div xmlns="{XHTML_NS}">{normalize_fragment(fragment)}</div>'
    return ET.fromstring(wrapped)


def replace_xhtml_value(the_value: ET.Element, fragment: str) -> None:
    tail = the_value.tail
    the_value.clear()
    the_value.tail = tail
    wrapper = parse_xhtml_fragment(fragment)
    the_value.text = wrapper.text
    for child in list(wrapper):
        wrapper.remove(child)
        the_value.append(child)
