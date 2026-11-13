"""Egress classification (DD-112, Tools LLD §4).

A tool is an egress channel if invoking it can cause data to leave the machine,
*regardless of its permission tier*. Three classifications in this module are
non-obvious and were missed by the original tier-based design:

* ``calendar_create`` — adding an attendee sends them an invitation carrying the
  event title and description. It looks like a local write.
* ``web_search`` / ``gmail_search`` — the *query* is outbound data. A search
  reads results but writes the query.
* ``shell_exec`` — no argument inspection makes it safe; ``curl`` is one command
  away. It can never be classified non-egress.

The default is ``UNKNOWN``, which behaves as ``ALWAYS``. A tool cannot
accidentally be non-egress.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from panini.security.types import EgressClass, EgressEval, ToolSpec

PreviewFn = Callable[[str, int], str]


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… ({len(text) - limit} more characters)"


class SyncedRootRegistry:
    """Paths synchronised off-machine by another process (OneDrive, Dropbox, …).

    Resolution failures and ambiguity are treated as *synced*. Guessing wrong in
    the permissive direction would silently create an exfiltration path.
    """

    def __init__(self, roots: Iterable[Path] | None = None) -> None:
        self._roots = [Path(r).resolve(strict=False) for r in (roots or ())]

    def root_for(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            # Cannot resolve → cannot prove it is local → assume synced.
            return self._roots[0] if self._roots else Path("<unresolvable>")
        for root in self._roots:
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return root
        return None

    def is_synced(self, path: Path) -> bool:
        return self.root_for(path) is not None


class EgressClassifier:
    """Resolves a tool spec plus arguments into an egress evaluation."""

    def __init__(self, synced_roots: SyncedRootRegistry | None = None) -> None:
        self._synced = synced_roots or SyncedRootRegistry()
        self._predicates: dict[str, Callable[[dict[str, object], bool], EgressEval]] = {
            "fs_write_synced": self._fs_write_synced,
            "query_carries_content": self._query_carries_content,
            "calendar_has_attendees": self._calendar_has_attendees,
        }

    def classify(self, spec: ToolSpec, args: dict[str, object], tainted: bool) -> EgressEval:
        cls = spec.egress_class

        if cls is EgressClass.NONE:
            return EgressEval.none()

        if cls in (EgressClass.ALWAYS, EgressClass.UNKNOWN):
            # UNKNOWN is not "undecided" — it is "unproven safe", so it is
            # treated exactly as ALWAYS.
            return EgressEval(
                is_egress=True,
                destination=self._destination_hint(spec, args),
                payload_preview=self._generic_preview(args),
            )

        predicate = self._predicates.get(spec.egress_predicate or "")
        if predicate is None:
            # A CONDITIONAL tool whose predicate is missing or unregistered is
            # treated as egress rather than trusted. Fail closed.
            return EgressEval(
                is_egress=True,
                destination=self._destination_hint(spec, args),
                payload_preview=self._generic_preview(args),
            )
        return predicate(args, tainted)

    # ── conditional predicates ───────────────────────────────────────────────

    def _fs_write_synced(self, args: dict[str, object], tainted: bool) -> EgressEval:
        """A write into a synchronised folder is exfiltration wearing a local costume.

        The path is resolved *before* the check so a symlink into OneDrive cannot
        evade it (HLD §9.2).
        """
        raw = str(args.get("path", ""))
        if not raw:
            return EgressEval(is_egress=True, destination="<unspecified path>")
        path = Path(raw)
        root = self._synced.root_for(path)
        if root is None:
            return EgressEval.none()
        return EgressEval(
            is_egress=True,
            destination=f"synced:{root}",
            payload_preview=_truncate(str(args.get("content", "")), 500),
        )

    def _query_carries_content(self, args: dict[str, object], tainted: bool) -> EgressEval:
        """A search query is outbound data.

        Only treated as egress on a tainted turn: on a clean turn the query is
        the user's own words, which they authored and can see.
        """
        if not tainted:
            return EgressEval.none()
        query = str(args.get("query", ""))
        return EgressEval(
            is_egress=True,
            destination=str(args.get("provider", "search provider")),
            payload_preview=_truncate(query, 200),
        )

    def _calendar_has_attendees(self, args: dict[str, object], tainted: bool) -> EgressEval:
        """Attendees receive an invitation carrying title and description.

        The preview deliberately includes the description — that is the field an
        exfiltration attempt would use, and truncating it would hide the payload.
        """
        attendees = args.get("attendees") or []
        if not attendees:
            return EgressEval.none()
        names = ", ".join(str(a) for a in attendees)  # type: ignore[union-attr]
        body = f"{args.get('title', '')} — {args.get('description', '')}"
        return EgressEval(
            is_egress=True,
            destination=names,
            payload_preview=_truncate(body, 500),
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _destination_hint(self, spec: ToolSpec, args: dict[str, object]) -> str | None:
        for key in ("to", "recipient", "url", "host", "destination", "server"):
            if key in args:
                return str(args[key])
        return None

    def _generic_preview(self, args: dict[str, object]) -> str | None:
        for key in ("body", "content", "command", "query", "text"):
            if key in args:
                return _truncate(str(args[key]), 500)
        return None
