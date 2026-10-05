import json

import pytest

from backend.teach.codegen_parser import parse
from backend.teach.parameterize import propose
from backend.teach.schema import Replay, Target, input_values


@pytest.mark.parametrize(
    "statement,tool",
    [
        ('page.goto("https://shop.example/")', "browser_navigate"),
        ('page.get_by_role("button", name="Add to cart").click()', "browser_click"),
        ('await page.get_by_role("button", name="Add to cart").dblclick()', "browser_click"),
        ('page.get_by_label("Search").fill("apples")', "browser_type"),
        ('page.get_by_placeholder("Query").press("Enter")', "browser_press_key"),
        ('page.get_by_role("combobox", name="Color").select_option("green")', "browser_select_option"),
        ('page.get_by_role("checkbox", name="Subscribe").check()', "browser_click"),
        ('page.get_by_role("checkbox", name="Subscribe").uncheck()', "browser_click"),
        ('page.get_by_text("Menu").hover()', "browser_hover"),
        ('page.get_by_label("Attachment").set_input_files("D:/demo.txt")', "browser_file_upload"),
    ],
)
def test_supported_literals(statement, tool):
    step = parse(statement)[0]
    assert step["tool"] == tool
    assert step["id"] == "s1"


def test_frame_keeps_scope_for_review():
    selector = 'iframe[name="report"]'
    step = parse('page.frame_locator(\'iframe[name="report"]\').get_by_role("button", name="Save").click()')[
        0
    ]
    assert step["target"]["frame"] == selector
    pinned = parse(
        'page.locator("iframe[name=\\"Report\\"]").content_frame.get_by_role("button", name="Save").click()'
    )[0]
    assert pinned["tool"] == "browser_click" and pinned["target"]["frame"] == 'iframe[name="Report"]'
    assert (
        parse('page.locator("iframe").content_frame.get_by_role("button", name="Save").click()')[0]["tool"]
        == "manual"
    )


@pytest.mark.parametrize(
    "source",
    [
        'page.get_by_label("Password").fill("orange")',
        'page.get_by_label("Card number").fill("4242424242424242")',
        'page.get_by_label("Search").fill("sk-example_secret_value123")',
        'page.get_by_label("Search").fill(__import__("os").system("do not execute"))',
        'page.evaluate("dangerous code")',
        'x = "private raw source must disappear"',
        "bad syntax (",
    ],
)
def test_unknown_and_sensitive_source_never_survives(source):
    result = parse(source)
    assert result[0]["tool"] == "manual"
    assert "orange" not in json.dumps(result)
    assert "4242" not in json.dumps(result)
    assert "private raw" not in json.dumps(result)
    assert "dangerous code" not in json.dumps(result)


def test_inputs_have_distinct_names_and_types():
    output = propose(
        parse(
            'page.get_by_label("Query").fill("apples")\npage.get_by_label("Query").fill("pears")\npage.get_by_label("Date").fill("2026-10-04")'
        )
    )
    replay = Replay.model_validate(output)
    assert list(replay.inputs) == ["query", "query_2", "date"]
    assert replay.inputs["date"].kind == "date"
    assert input_values(replay, {"query": "oranges"})["query"] == "oranges"
    with pytest.raises(ValueError, match="Unknown"):
        input_values(replay, {"wrong": "x"})
    with pytest.raises(ValueError, match="Secrets"):
        input_values(replay, {"query": "password=orange"})


def test_bounds_and_no_regex_execution():
    with pytest.raises(ValueError):
        parse("x" * 120001)
    with pytest.raises(ValueError):
        Target(name_regex="^(a+)+$")
    assert Target(name_regex="^Report").name_regex == "^Report"


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "s1", "tool": "browser_evaluate", "args": {}},
        {"id": "s1", "tool": "browser_click", "args": {"target": "e5"}, "target": {"name": "Save"}},
        {"id": "s1", "tool": "browser_type", "args": {"text": "{{unknown}}"}, "target": {"name": "Query"}},
        {"id": "s1", "tool": "browser_type", "args": {"text": "orange"}, "target": {"name": "Passcode"}},
    ],
)
def test_replay_rejects_unsafe_declarative_data(bad):
    with pytest.raises(ValueError):
        Replay.model_validate({"steps": [bad]})
