"""UIAutomator XML to observation-scoped refs; no credential values leave this parser."""

import re
from dataclasses import dataclass
from xml.etree import ElementTree

from .snapshot import Node, render, role

PASSWORD = re.compile(
    r"password|passcode|one.time|\bpin\b|\bcvv\b|\bcvc\b|\botp\b|credit.card|card.number|security.code|verification.code",
    re.I,
)
BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


@dataclass
class AndroidNode:
    node: Node
    package: str
    resource_id: str
    class_name: str
    description: str
    text: str
    focused: bool

    def identity(self):
        return (
            self.package,
            self.resource_id,
            self.class_name,
            self.description,
            self.text,
            self.node.box,
            frozenset(self.node.states),
            self.focused,
        )

    def selector(self):
        result = {"clazz": self.class_name}
        if self.resource_id:
            result["res"] = self.resource_id
        if self.description:
            result["desc"] = self.description
        if self.text:
            result["text"] = self.text
        result["pkg"] = self.package
        return result


def tree(xml, generation, depth=20):
    if not isinstance(xml, str) or len(xml) > 1_000_000 or "<!" in xml:
        raise ValueError("The phone returned invalid or oversized XML.")
    start, end = xml.find("<hierarchy"), xml.rfind("</hierarchy>")
    if start < 0 or end < 0:
        raise ValueError("No Android accessibility hierarchy was returned.")
    try:
        root = ElementTree.fromstring(xml[start : end + len("</hierarchy>")])
    except ElementTree.ParseError:
        raise ValueError("The Android accessibility hierarchy is malformed.") from None
    nodes, packages, stack = {}, set(), [(child, 0) for child in reversed(list(root))]
    count = 0
    while stack:
        element, level = stack.pop()
        count += 1
        if count > 600:
            raise ValueError("The Android hierarchy exceeds 600 nodes; no actions are allowed.")
        a = element.attrib
        package = a.get("package", "")
        if package:
            packages.add(package)
        bounds = BOUNDS.fullmatch(a.get("bounds", ""))
        if not bounds:
            raise ValueError("The Android hierarchy contains invalid bounds.")
        x1, y1, x2, y2 = map(int, bounds.groups())
        if x2 < x1 or y2 < y1 or x2 > 10000 or y2 > 10000:
            raise ValueError("The Android hierarchy contains invalid bounds.")
        name = a.get("content-desc") or a.get("text", "")
        states = {s for s in ("checked", "selected") if a.get(s) == "true"}
        if a.get("enabled") != "true":
            states.add("disabled")
        if a.get("password") == "true" or PASSWORD.search(name + " " + a.get("resource-id", "")):
            states.add("password")
        text = "" if "password" in states else a.get("text", "")
        # Password text may also be the accessible name. Never retain either.
        if "password" in states:
            name = "Password field"
        ref = f"a{generation * 1000 + count}"
        n = Node(
            ref,
            role(a.get("class", "")),
            name[:500],
            text if role(a.get("class", "")) == "textbox" else None,
            states,
            (x1, y1, x2 - x1, y2 - y1),
            min(level, 30),
        )
        nodes[ref] = AndroidNode(
            n,
            package,
            a.get("resource-id", ""),
            a.get("class", ""),
            "" if "password" in states else a.get("content-desc", "")[:500],
            text[:10000],
            a.get("focused") == "true",
        )
        stack.extend((child, level + 1) for child in reversed(list(element)))
    shown = [n.node for n in nodes.values() if n.node.depth <= depth]
    return nodes, packages, render(shown)
