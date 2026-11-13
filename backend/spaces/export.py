"""Owner exports: validate durable sources before saving; render PDFs without network access."""

import asyncio
import json
import os
import signal
import subprocess

from ..hands.playwright_mcp import SIDECAR, clean_env, node_executable
from .store import SpacesError

PDF_SLOTS = asyncio.Semaphore(2)


def save_source(h, *, space_id, kind, source_id, title=None, parent_id=None):
    with h.spaces.transaction():
        h.spaces.authorize(space_id)
        cache_source_id = source_id
        conversation_run = None
        skill = None
        if kind == "conversation":
            binding = h.store.one(
                "SELECT thread_id FROM thread_bindings WHERE surface='agui' AND external_id=?", (source_id,)
            )
            try:
                runs = h.runs.list(thread_id=binding["thread_id"], limit=1) if binding else []
            except (ValueError, TypeError):
                raise SpacesError("SOURCE_UNAVAILABLE", "Conversation run is malformed.", 409) from None
            conversation_run = runs[0] if runs else None
            if conversation_run:
                cache_source_id += ":" + conversation_run["id"]
        elif kind == "skill":
            try:
                skill = h.teach.bundles.current(source_id)
            except ValueError:
                raise SpacesError("SOURCE_UNAVAILABLE", "No approved skill with that name.", 409) from None
            cache_source_id += ":" + str(skill[0]["version"])
        prior = h.store.one(
            "SELECT page_id FROM page_sources WHERE space_id=? AND kind=? AND source_id=?",
            (space_id, kind, cache_source_id),
        )
        if prior:
            return h.spaces.read(prior["page_id"], deleted=True)
        if kind == "skill":
            row, replay = skill
            name = "Skill: " + source_id
            thread = None
            inputs = ", ".join(replay.inputs) or "none"
            content = (
                f"# {name}\n\n{row['description']}\n\n"
                f"Reviewed version: {row['version']} · kind: {replay.kind}\n\n"
                f"Inputs: {inputs}\n\n"
                f"This page describes an approved demonstration. Replay remains in Teach and follows current permissions.\n\n"
                f"[Source skill](/build/skills?skill={source_id})\n"
            )
        elif kind == "task":
            try:
                task = h.tasks.get(source_id)
            except KeyError:
                raise SpacesError("SOURCE_UNAVAILABLE", "Task not found.", 404) from None
            except (ValueError, TypeError):
                raise SpacesError("SOURCE_UNAVAILABLE", "Task record is malformed.", 409) from None
            if task["status"] != "completed":
                raise SpacesError("SOURCE_UNAVAILABLE", "Only completed tasks can be saved.", 409)
            try:
                runs = h.runs.list(task_id=source_id, limit=1)
            except (ValueError, TypeError):
                raise SpacesError("SOURCE_UNAVAILABLE", "Task run is malformed.", 409) from None
            if not runs or runs[0]["state"] != "completed":
                raise SpacesError("SOURCE_UNAVAILABLE", "Task has no completed durable run.", 409)
            run = runs[0]
            result = run.get("result")
            if (
                not isinstance(result, dict)
                or result.get("outcome") != "verified_complete"
                or result.get("result_truncated")
            ):
                raise SpacesError("SOURCE_UNAVAILABLE", "Task has no verified completed result.", 409)
            content = result.get("message")
            h.spaces._text(content, 200000, "Result", True)
            name, thread = task["title"], run.get("thread_id")
            content += f"\n\nSource task: `{source_id}` · run: `{run['id']}`\n"
        elif kind == "receipt":
            record = h.ledger.receipt(source_id)
            if not record or record["outcome"] == "pending":
                raise SpacesError("SOURCE_UNAVAILABLE", "Receipt is missing or still pending.", 409)
            name = f"Receipt: {record['tool']}"
            content = (
                f"# {name}\n\nOutcome: {record['outcome']}\n\nEffect: {record['effect']}\n\n"
                f"## Redacted arguments\n\n```json\n{json.dumps(record['args_view'], ensure_ascii=False, indent=2)}\n```\n\n"
                f"## Recorded result preview\n\n{record['result_view'] or '(No result recorded)'}\n\n"
                f"## Evidence\n\n```json\n{json.dumps(record['evidence'], ensure_ascii=False, indent=2)}\n```\n\n"
                f"[Source receipt](/receipts?receipt={source_id})\n"
            )
            thread = record.get("thread_id")
        elif kind in {"result", "history", "conversation"}:
            try:
                if kind == "conversation":
                    record = conversation_run
                else:
                    record = h.runs.get(source_id)
            except (ValueError, TypeError):
                record = None
            if not record or record["state"] != "completed":
                raise SpacesError("SOURCE_UNAVAILABLE", "Only completed runs can be saved.", 409)
            result = record.get("result")
            outcomes = {"verified_complete"} if kind == "result" else {"verified_complete", "reply"}
            if not isinstance(result, dict) or result.get("outcome") not in outcomes:
                raise SpacesError("SOURCE_UNAVAILABLE", "Run has no verified completed result.", 409)
            if kind == "result" and result.get("result_truncated"):
                raise SpacesError("SOURCE_UNAVAILABLE", "Result exceeds its durable storage limit.", 409)
            thread = record.get("thread_id")
            name = "PANINI result" if kind == "result" else "PANINI conversation"
            if kind == "result":
                content = result.get("message")
                if not isinstance(content, str) or not content.strip():
                    raise SpacesError("SOURCE_UNAVAILABLE", "Result has no text.", 409)
            else:
                runs = h.store.all("SELECT state FROM runs WHERE thread_id=?", (thread,))
                if not thread or any(r["state"] not in {"completed", "resumed"} for r in runs):
                    raise SpacesError(
                        "SOURCE_UNAVAILABLE", "Conversation contains unfinished or failed runs.", 409
                    )
                try:
                    session = h.sessions.get(thread)
                    lines = [session.get("summary") or ""]
                    last_assistant = ""
                    for item in session["items"]:
                        if item.get("role") == "user" and isinstance(item.get("content"), str):
                            lines.append("## You\n\n" + item["content"])
                        elif item.get("type") == "message":
                            parts = item.get("content", [])
                            last_assistant = "".join(p.get("text", "") for p in parts)
                            lines.append("## PANINI\n\n" + last_assistant)
                    final = result.get("message")
                    if isinstance(final, str) and final and final != last_assistant:
                        lines.append("## PANINI\n\n" + final)
                    content = "\n\n".join(lines)
                except (KeyError, ValueError, TypeError, AttributeError):
                    raise SpacesError(
                        "SOURCE_UNAVAILABLE", "Conversation history is malformed.", 409
                    ) from None
            content += f"\n\nSource run: `{record['id']}`\n"
        else:
            raise SpacesError("INVALID_SOURCE", "Unsupported notebook source.")
        h.spaces._text(content, 200000, "Source content", True)
        page = h.spaces.create(space_id, title or name, content, parent_id=parent_id, source_thread_id=thread)
        h.store.execute(
            "INSERT INTO page_sources VALUES(?,?,?,?)", (space_id, kind, cache_source_id, page["id"])
        )
        return page


async def pdf(page):
    node = node_executable()
    if not node:
        raise SpacesError("PDF_UNAVAILABLE", "The bundled Node runtime is unavailable.", 503)
    async with PDF_SLOTS:
        process = await asyncio.create_subprocess_exec(
            node,
            str(SIDECAR / "page-pdf.mjs"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_env(),
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {"start_new_session": True}
            ),
        )
        try:
            output, _ = await asyncio.wait_for(
                process.communicate(json.dumps(page, ensure_ascii=False).encode()), 45
            )
        except BaseException as exc:
            if process.returncode is None:
                if os.name == "nt":
                    cleanup = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await cleanup.wait()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            if isinstance(exc, TimeoutError):
                raise SpacesError("PDF_TIMEOUT", "PDF rendering timed out. Try a smaller page.", 503) from exc
            raise
        if process.returncode or not output.startswith(b"%PDF-") or len(output) > 20_000_000:
            raise SpacesError(
                "PDF_UNAVAILABLE", "PDF rendering failed. Check that Edge or Chrome is installed.", 503
            )
        return output
