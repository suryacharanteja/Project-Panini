"""Parse pinned Playwright Python code with AST. Never compile, evaluate or execute it.

Unknown syntax is a manual step with a generic note; its raw source is never stored.
"""

import ast
import re

from ..harness.memory import _SECRET
from .schema import SENSITIVE, frame_name


def literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool, int)):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [literal(n) for n in node.elts]
    if isinstance(node, ast.Dict):
        return {literal(k): literal(v) for k, v in zip(node.keys, node.values, strict=True)}
    raise ValueError("Non-literal argument")


def locator(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        raise ValueError("Unsupported locator")
    method = node.func.attr
    args = [literal(a) for a in node.args]
    kw = {k.arg: literal(k.value) for k in node.keywords}
    if method == "get_by_role" and args and isinstance(args[0], str):
        result = {"role": args[0], "name": kw.get("name", "")}
    elif (
        method in ("get_by_label", "get_by_text", "get_by_placeholder", "get_by_alt_text", "get_by_title")
        and args
    ):
        result = {"name": args[0]}
    else:
        raise ValueError("Locator needs an accessible role or name")
    if not isinstance(result["name"], str) or not result["name"]:
        raise ValueError("Unnamed target")
    receiver = node.func.value
    if isinstance(receiver, ast.Call):
        if isinstance(receiver.func, ast.Attribute) and receiver.func.attr == "frame_locator":
            result["frame"] = literal(receiver.args[0])
        else:
            raise ValueError("Unsupported chained locator")
    elif isinstance(receiver, ast.Attribute) and receiver.attr == "content_frame":
        # Pinned Playwright Python codegen emits page.locator(...).content_frame.
        call = receiver.value
        if (
            not isinstance(call, ast.Call)
            or not isinstance(call.func, ast.Attribute)
            or call.func.attr != "locator"
            or not isinstance(call.func.value, ast.Name)
            or not re.fullmatch(r"page\d*", call.func.value.id)
            or len(call.args) != 1
            or call.keywords
        ):
            raise ValueError("Unsupported frame locator")
        result["frame"] = literal(call.args[0])
    elif not isinstance(receiver, ast.Name) or not re.fullmatch(r"page\d*", receiver.id):
        raise ValueError("Unsupported page")
    if "frame" in result:
        frame_name(result["frame"])
    result["locator"] = ast.unparse(node)
    return result


def parse(code):
    if not isinstance(code, str) or len(code) > 120000:
        raise ValueError("Recording exceeds the parser limit.")
    fenced = re.search(r"```python\s*\n(.*?)\n```", code, re.S)
    if fenced:
        code = fenced[1]
    try:
        tree = ast.parse(code)
    except (SyntaxError, RecursionError):
        return [
            {
                "id": "s1",
                "tool": "manual",
                "note": "The recorder output could not be normalized. Record again.",
            }
        ]
    steps = []
    for stmt in tree.body:
        if len(steps) >= 100:
            raise ValueError("Recording contains more than 100 steps.")
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        try:
            expr = stmt.value if isinstance(stmt, ast.Expr) else None
            expr = expr.value if isinstance(expr, ast.Await) else expr
            if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Attribute):
                raise ValueError("Unsupported statement")
            method = expr.func.attr
            args = [literal(a) for a in expr.args]
            kw = {k.arg: literal(k.value) for k in expr.keywords}
            if method == "goto" and isinstance(expr.func.value, ast.Name) and args:
                step = {"tool": "browser_navigate", "args": {"url": args[0]}}
            else:
                target = locator(expr.func.value)
                if SENSITIVE.search(str(target)) or _SECRET.search(str(args)):
                    raise ValueError("Sensitive input")
                step = {"target": target, "args": {}}
                if method in ("click", "dblclick", "check", "uncheck"):
                    step["tool"] = "browser_click"
                    if method == "dblclick":
                        step["args"]["doubleClick"] = True
                    if method in ("check", "uncheck"):
                        step["checked"] = method == "check"
                    if kw.keys() - {"button", "modifiers", "timeout"}:
                        raise ValueError("Unsupported click options")
                    step["args"].update({k: v for k, v in kw.items() if k != "timeout"})
                elif method == "fill" and len(args) == 1:
                    step.update(tool="browser_type", args={"text": args[0], "submit": False})
                elif method == "press" and len(args) == 1:
                    step.update(tool="browser_press_key", args={"key": args[0]})
                elif method == "select_option" and (args or "value" in kw):
                    values = args[0] if args else kw["value"]
                    if isinstance(values, dict):
                        values = values.get("value")
                    if not isinstance(values, (str, list)):
                        raise ValueError("Selection needs option values")
                    step.update(
                        tool="browser_select_option",
                        args={"values": [values] if isinstance(values, str) else values},
                    )
                elif method == "hover":
                    step["tool"] = "browser_hover"
                elif method == "set_input_files" and args:
                    step.update(
                        tool="browser_file_upload",
                        args={"paths": [args[0]] if isinstance(args[0], str) else args[0]},
                    )
                else:
                    raise ValueError("Unsupported action")
            if _SECRET.search(str(step)):
                raise ValueError("Sensitive data")
        except (ValueError, TypeError, IndexError):
            step = {
                "tool": "manual",
                "note": "Unsupported or sensitive action: correct or remove this step before replay.",
            }
        steps.append({"id": f"s{len(steps) + 1}", **step})
    return steps or [{"id": "s1", "tool": "manual", "note": "No actions were recorded."}]
