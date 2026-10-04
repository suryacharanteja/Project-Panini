"""Bounded snapshot vocabulary shared by Windows, Android and sandbox hands (V2-26)."""

import json
import re
from dataclasses import dataclass, field

PREFIXES = frozenset("ewas")
STATES = ("checked", "selected", "expanded", "disabled", "password")
ROLES = {
    "Button": "button",
    "SplitButton": "button",
    "Edit": "textbox",
    "EditText": "textbox",
    "Document": "document",
    "CheckBox": "checkbox",
    "Switch": "checkbox",
    "RadioButton": "radio",
    "ComboBox": "combobox",
    "List": "list",
    "RecyclerView": "list",
    "ListView": "list",
    "ListItem": "listitem",
    "Menu": "menu",
    "MenuBar": "menubar",
    "MenuItem": "menuitem",
    "Tab": "tablist",
    "TabItem": "tab",
    "Tree": "tree",
    "TreeItem": "treeitem",
    "DataGrid": "grid",
    "Table": "grid",
    "DataItem": "row",
    "Hyperlink": "link",
    "Text": "text",
    "TextView": "text",
    "Slider": "slider",
    "Spinner": "spinbutton",
    "ScrollBar": "scrollbar",
    "Window": "window",
    "Pane": "group",
    "Group": "group",
    "Custom": "generic",
    "ImageButton": "button",
}


def role(class_name):
    return ROLES.get(class_name.rsplit(".", 1)[-1], "generic")


@dataclass
class Node:
    ref: str
    role: str
    name: str = ""
    value: str | None = None
    states: set[str] = field(default_factory=set)
    box: tuple[int, int, int, int] | None = None
    depth: int = 0


def render(nodes):
    if len(nodes) > 600:
        raise ValueError("Snapshots are limited to 600 nodes.")
    lines, seen = [], set()
    for n in nodes:
        if not re.fullmatch(r"[ewas][0-9]+", n.ref) or n.ref in seen:
            raise ValueError("Invalid or duplicate snapshot ref.")
        seen.add(n.ref)
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", n.role) or not 0 <= n.depth <= 30:
            raise ValueError("Invalid snapshot role or depth.")
        line = "  " * n.depth + f"- {n.role} " + json.dumps(n.name[:500], ensure_ascii=False)
        line += f" [ref={n.ref}]"
        if n.value is not None and "password" not in n.states:
            line += " [value=" + json.dumps(n.value[:100], ensure_ascii=False) + "]"
        for state in STATES:
            if state in n.states:
                line += f" [{state}]"
        if n.box:
            line += " [box=" + ",".join(map(str, n.box)) + "]"
        lines.append(line)
    return "\n".join(lines)


def parse(text):
    """Parse our grammar strictly; JSON strings may contain brackets, quotes and newlines."""
    if len(text) > 600_000:
        raise ValueError("Snapshot too large.")
    decoder, nodes = json.JSONDecoder(), []
    for line in text.splitlines():
        match = re.match(r"^( *)- ([a-z][a-z0-9_-]*) ", line)
        if not match or len(match[1]) % 2:
            raise ValueError("Invalid snapshot line.")
        rest = line[match.end() :]
        name, end = decoder.raw_decode(rest)
        if not isinstance(name, str):
            raise ValueError("Invalid node name.")
        rest = rest[end:]
        ref = re.match(r" \[ref=([ewas][0-9]+)\]", rest)
        if not ref:
            raise ValueError("Missing snapshot ref.")
        node = Node(ref[1], match[2], name, depth=len(match[1]) // 2)
        rest = rest[ref.end() :]
        while rest:
            if rest.startswith(" [value="):
                value, end = decoder.raw_decode(rest[8:])
                if not isinstance(value, str) or rest[8 + end : 9 + end] != "]":
                    raise ValueError("Invalid node value.")
                node.value, rest = value, rest[9 + end :]
            else:
                attr = re.match(r" \[([^]]+)\]", rest)
                if not attr:
                    raise ValueError("Invalid node attribute.")
                if attr[1] in STATES:
                    node.states.add(attr[1])
                elif re.fullmatch(r"box=-?\d+,-?\d+,\d+,\d+", attr[1]):
                    node.box = tuple(map(int, attr[1][4:].split(",")))
                else:
                    raise ValueError("Unknown node attribute.")
                rest = rest[attr.end() :]
        if "password" in node.states:
            node.value = None
        nodes.append(node)
    render(nodes)  # Apply the same ref, role and size checks as serialization.
    return nodes
