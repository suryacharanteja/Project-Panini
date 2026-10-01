import asyncio

import pytest

from backend.harness.mcp_tools import list_tools, tool_result


def test_catalogue_walks_pages_and_requires_a_boolean_read_only_hint():
    async def request(method, params):
        assert method == "tools/list"
        if not params:
            return {
                "tools": [{"name": "read", "annotations": {"readOnlyHint": True}}],
                "nextCursor": "second",
            }
        assert params == {"cursor": "second"}
        return {"tools": [{"name": "write", "annotations": {"readOnlyHint": "false"}}]}

    tools = asyncio.run(list_tools(request))
    assert [(tool["name"], tool["read_only"]) for tool in tools] == [("read", True), ("write", False)]


def test_repeated_pagination_is_rejected():
    async def request(_method, params):
        return {"tools": [{"name": "a" if not params else "b"}], "nextCursor": "same"}

    with pytest.raises(ValueError, match="repeated pagination"):
        asyncio.run(list_tools(request))


def test_structured_only_tool_results_are_preserved_and_bounded():
    result = tool_result({"structuredContent": {"records": [{"title": "Launch", "id": 12}]}})
    assert not result["is_error"] and '"id": 12' in result["text"]
    assert len(tool_result({"structuredContent": {"body": "a" * 30000}})["text"]) == 20000


def test_malformed_results_are_not_successful_receipts():
    with pytest.raises(ValueError, match="invalid tool result"):
        tool_result(None)
