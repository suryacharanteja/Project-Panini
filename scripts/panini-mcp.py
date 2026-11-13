"""PANINI stdio ↔ local Streamable HTTP bridge. stdout is MCP protocol only."""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, help="Client id issued in PANINI Admin")
    parser.add_argument("--url", default="http://127.0.0.1:3200/mcp")
    parser.add_argument("--token-file", type=Path, help="Override the protected token file location")
    return parser.parse_args()


def credential(args):
    if len(args.client) != 32 or any(c not in "0123456789abcdef" for c in args.client):
        raise ValueError("Use the client id shown in PANINI Admin.")
    url = urlsplit(args.url)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.path != "/mcp"
        or url.query
        or url.fragment
        or url.username
        or url.password
    ):
        raise ValueError("PANINI MCP requires an exact loopback HTTP /mcp endpoint.")
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "PANINI/mcp"
    path = args.token_file or root / args.client / "client.token"
    if path.is_symlink() or path.stat().st_size > 256:
        raise ValueError("Invalid client token file.")
    token = path.read_text(encoding="utf-8").strip()
    if not token.startswith("mcp_") or len(token) < 40 or any(c.isspace() for c in token):
        raise ValueError("Invalid client token.")
    return token


async def bridge(args):
    token = credential(args)
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + token}, follow_redirects=False
    ) as http:
        async with streamable_http_client(args.url, http_client=http) as (http_read, http_write):
            async with ClientSession(http_read, http_write, read_timeout_seconds=950) as client:
                await client.initialize()

                async def list_tools(ctx, params):
                    return await client.list_tools(params=params)

                async def call_tool(ctx, params):
                    async def progress(value, total, message):
                        await ctx.session.report_progress(value, total=total, message=message)

                    return await client.call_tool(
                        params.name, params.arguments, progress_callback=progress, read_timeout_seconds=950
                    )

                server = Server("PANINI", version="2.0.0", on_list_tools=list_tools, on_call_tool=call_tool)
                async with stdio_server() as (read, write):
                    await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        asyncio.run(bridge(arguments()))
    except (Exception, KeyboardInterrupt):
        print(
            "PANINI MCP bridge stopped. Check the client id, token file and local runtime.", file=sys.stderr
        )
        sys.exit(1)
