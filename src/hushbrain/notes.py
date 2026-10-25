"""Parse one markdown note with YAML frontmatter into a Note."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter

from hushbrain.policy import Policy
from hushbrain.taxonomy import NODE_TYPES

log = logging.getLogger("hushbrain.notes")

_KNOWN_KEYS = {
    "id", "type", "title", "summary", "created", "updated", "edges",
    "project_id", "project", "tags", "sensitivity",
}
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def safe_name(name: str) -> str:
    """A stable short label for logs. Note ids can themselves be private, so logs carry this."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


def content_hash(summary: str, body: str) -> str:
    """sha256(summary NUL (body or summary)).

    It covers exactly the two strings that get embedded, so an unchanged hash means both
    embeddings can be skipped. Frontmatter-only edits (a re-label, new edges) leave it
    unchanged, which is why the scalar-update path must still write the sensitivity.
    """
    h = hashlib.sha256()
    h.update((summary or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((body or summary or "").encode("utf-8"))
    return h.hexdigest()


@dataclass
class Note:
    path: Path
    node_id: str
    ntype: str
    title: str
    summary: str
    body: str
    sensitivity: str
    # How the tier was reached: explicit, missing, invalid, or project-floor
    sensitivity_reason: str = "explicit"
    project_id: str | None = None
    created: str | None = None
    updated: str | None = None
    edges: list[dict[str, str]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def source_file(self) -> str:
        return self.path.name


def _date(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    m = _DATE_RE.match(s)
    return m.group(1) if m else s


def parse_note(path: Path, policy: Policy) -> Note | None:
    try:
        post = frontmatter.load(path)
    except Exception as e:  # malformed YAML, unreadable file, bad encoding
        log.warning("frontmatter parse failed for %s: %s", safe_name(path.name), type(e).__name__)
        return None

    fm = post.metadata or {}
    body = post.content or ""
    stem = path.stem

    node_id = str(fm.get("id") or stem)
    ntype = str(fm.get("type") or "note").lower()
    if ntype not in NODE_TYPES:
        ntype = "note"
    title = str(fm.get("title") or stem)

    summary = str(fm.get("summary") or "").strip()
    if not summary:
        # Derive from the first prose line: the summary is what retrieval runs on.
        for line in body.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", ">")):
                summary = s[:200]
                break
    if not summary:
        summary = title

    project_id = fm.get("project_id") or fm.get("project")
    project_id = str(project_id) if project_id is not None else None

    tier, reason = policy.resolve_tier(fm.get("sensitivity"))
    tier, raised = policy.apply_project_floor(tier, project_id)
    if raised:
        reason = "project-floor"

    edges: list[dict[str, str]] = []
    raw_edges = fm.get("edges") or []
    if isinstance(raw_edges, list):
        for e in raw_edges:
            if isinstance(e, dict):
                edges.append({
                    "type": str(e.get("type", "")).lower(),
                    "target": str(e.get("target", "")),
                    "note": str(e.get("note") or ""),
                })

    extra = {k: v for k, v in fm.items() if k not in _KNOWN_KEYS}
    if "tags" in fm:
        extra["tags"] = fm["tags"]

    created = _date(fm.get("created"))
    return Note(
        path=path, node_id=node_id, ntype=ntype, title=title, summary=summary, body=body,
        sensitivity=tier, sensitivity_reason=reason, project_id=project_id,
        created=created, updated=_date(fm.get("updated")) or created,
        edges=edges, extra=extra,
    )
