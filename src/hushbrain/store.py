"""All SQL lives here, behind one visibility rule.

Every read goes through `_visibility()`, and every read tool takes `include_sensitive`
defaulting to False. get_body is the sharpest case: it is the only call that returns full
text, so it refuses a hidden note instead of returning it.

Two properties are worth stating plainly:

* The default filter is an allow-list (`sensitivity = ANY(visible tiers)`), not a deny-list.
  A row whose tier is something the policy has never heard of is therefore hidden, which
  is the right way to fail.
* `include_sensitive` must be the literal boolean True to count. A string "false" from a
  careless client is truthy in Python, so it is treated as False here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hushbrain.config import Settings
from hushbrain.db import Database
from hushbrain.embeddings import Embedder, vec_literal
from hushbrain.notes import Note
from hushbrain.policy import Policy
from hushbrain.taxonomy import NODE_TYPES


def _clamp(value: int | None, default: int, hi: int) -> int:
    if not value or value <= 0:
        return default
    return min(value, hi)


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


class MemoryStore:
    def __init__(self, db: Database, policy: Policy, settings: Settings, embedder: Embedder) -> None:
        self.db = db
        self.policy = policy
        self.settings = settings
        self.embedder = embedder

    # ---- visibility ---------------------------------------------------------------

    def _allowed(self, include_sensitive: Any) -> bool:
        return include_sensitive is True and not self.settings.lock_sensitive

    def _locked_out(self, include_sensitive: Any) -> bool:
        """True when the caller asked for sensitive notes and the server refuses to give them."""
        return include_sensitive is True and self.settings.lock_sensitive

    def _visibility(self, include_sensitive: Any) -> tuple[str, list[Any]]:
        if self._allowed(include_sensitive):
            return "TRUE", []
        return "sensitivity = ANY(%s)", [list(self.policy.default_visible)]

    def _trim(self, s: str | None) -> str:
        """One line, at most summary_chars long."""
        if not s:
            return ""
        s = " ".join(s.split())
        n = self.settings.summary_chars
        return s if len(s) <= n else s[: n - 1] + "…"

    def _tag(self, out: dict[str, Any], include_sensitive: Any) -> dict[str, Any]:
        if self._locked_out(include_sensitive):
            out["sensitive_locked"] = True
        return out

    def _card(self, r: dict[str, Any], *, similarity: bool = False) -> dict[str, Any]:
        card = {
            "id": r["id"],
            "type": r["type"],
            "title": r["title"],
            "summary": self._trim(r["summary"]),
            "project_id": r["project_id"],
            "sensitivity": r["sensitivity"],
            "updated_at": _iso(r["updated_at"]),
        }
        if similarity:
            card["similarity"] = round(float(r["similarity"]), 3)
        return card

    # ---- read tools ---------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int | None = None,
        type: str | None = None,
        project_id: str | None = None,
        include_sensitive: bool = False,
    ) -> dict[str, Any]:
        if not query or not query.strip():
            return {"error": "query is empty"}
        if type and type not in NODE_TYPES:
            return {"error": f"unknown type '{type}'", "valid": sorted(NODE_TYPES)}
        n = _clamp(limit, self.settings.default_search_limit, self.settings.max_search_limit)
        vec = vec_literal(self.embedder.embed(query))
        vis_sql, vis_params = self._visibility(include_sensitive)
        where = [vis_sql]
        params: list[Any] = [vec] + vis_params
        if type:
            where.append("type = %s")
            params.append(type)
        if project_id:
            where.append("project_id = %s")
            params.append(project_id)
        params += [vec, n]
        rows = self.db.conn().execute(
            f"""
            SELECT id, type, title, summary, project_id, sensitivity, updated_at,
                   1 - (summary_embedding <=> %s::vector) AS similarity
            FROM memories
            WHERE {' AND '.join(where)}
            ORDER BY summary_embedding <=> %s::vector
            LIMIT %s
            """,
            params,
        ).fetchall()
        return self._tag({
            "query": query,
            "filters": {"type": type, "project_id": project_id,
                        "include_sensitive": self._allowed(include_sensitive)},
            "count": len(rows),
            "results": [self._card(r, similarity=True) for r in rows],
        }, include_sensitive)

    def cross_project(
        self, query: str, limit: int | None = None, include_sensitive: bool = False
    ) -> dict[str, Any]:
        """Search with the project filter dropped. The sensitivity filter is not dropped."""
        return self.search(query, limit=limit, type=None, project_id=None,
                           include_sensitive=include_sensitive)

    def recent(
        self, limit: int | None = None, type: str | None = None, include_sensitive: bool = False
    ) -> dict[str, Any]:
        if type and type not in NODE_TYPES:
            return {"error": f"unknown type '{type}'", "valid": sorted(NODE_TYPES)}
        n = _clamp(limit, self.settings.default_search_limit, self.settings.max_search_limit)
        vis_sql, vis_params = self._visibility(include_sensitive)
        where = [vis_sql]
        params: list[Any] = list(vis_params)
        if type:
            where.append("type = %s")
            params.append(type)
        params.append(n)
        rows = self.db.conn().execute(
            f"""
            SELECT id, type, title, summary, project_id, sensitivity, updated_at
            FROM memories
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
        return self._tag(
            {"count": len(rows), "results": [self._card(r) for r in rows]}, include_sensitive
        )

    def project_context(
        self, project_id: str, limit: int | None = None, include_sensitive: bool = False
    ) -> dict[str, Any]:
        """A project's pillar (id `pillar-<project_id>`) first, then its decisions, questions, tasks."""
        n = _clamp(limit, 12, 30)
        vis_sql, vis_params = self._visibility(include_sensitive)
        rows = self.db.conn().execute(
            f"""
            SELECT id, type, title, summary, project_id, sensitivity, updated_at
            FROM memories
            WHERE (project_id = %s OR id = %s) AND {vis_sql}
            ORDER BY
                CASE type
                    WHEN 'pillar' THEN 0 WHEN 'decision' THEN 1
                    WHEN 'question' THEN 2 WHEN 'task' THEN 3 ELSE 9
                END,
                updated_at DESC
            LIMIT %s
            """,
            [project_id, f"pillar-{project_id}"] + vis_params + [n],
        ).fetchall()
        return self._tag({
            "project_id": project_id,
            "count": len(rows),
            "nodes": [self._card(r) for r in rows],
        }, include_sensitive)

    def what_changed_since(self, since_iso: str, include_sensitive: bool = False) -> dict[str, Any]:
        try:
            since = datetime.fromisoformat(since_iso)
        except (TypeError, ValueError):
            return {"error": "since_iso must be an ISO 8601 timestamp, e.g. 2026-05-01T00:00:00+00:00"}
        if since.tzinfo is None:
            # A naive timestamp would be read in the database session's zone; say UTC instead.
            since = since.replace(tzinfo=timezone.utc)
        vis_sql, vis_params = self._visibility(include_sensitive)
        rows = self.db.conn().execute(
            f"""
            SELECT id, type, title, summary, project_id, sensitivity, updated_at
            FROM memories
            WHERE updated_at > %s AND {vis_sql}
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            [since] + vis_params + [self.settings.changed_since_limit],
        ).fetchall()
        return self._tag({
            "since": since.isoformat(),
            "count": len(rows),
            "results": [self._card(r) for r in rows],
        }, include_sensitive)

    def get_body(self, id: str, include_sensitive: bool = False) -> dict[str, Any]:
        """The only call that returns full text. Refuses a hidden note and returns no content.

        The refusal names the tier on purpose: the caller learns the note exists and that a
        deliberate opt-in is needed, instead of concluding it does not exist.
        """
        r = self.db.conn().execute(
            """
            SELECT id, type, title, summary, body, project_id, sensitivity, created_at, updated_at
            FROM memories WHERE id = %s
            """,
            (id,),
        ).fetchone()
        if not r:
            return {"error": f"no node with id '{id}'"}
        if not self._allowed(include_sensitive) and r["sensitivity"] not in self.policy.default_visible:
            out: dict[str, Any] = {
                "error": "sensitive node withheld",
                "id": id,
                "sensitivity": r["sensitivity"],
            }
            if self.settings.lock_sensitive:
                out["hint"] = "This server is locked: include_sensitive is disabled for every caller."
            else:
                out["hint"] = (
                    "This note is private. Retry with include_sensitive=true only if the user has "
                    "said this conversation is about their private life."
                )
            return out
        return {
            "id": r["id"],
            "type": r["type"],
            "title": r["title"],
            "summary": r["summary"],
            "body": r["body"],
            "project_id": r["project_id"],
            "sensitivity": r["sensitivity"],
            "created_at": _iso(r["created_at"]),
            "updated_at": _iso(r["updated_at"]),
        }

    def stats(self, include_sensitive: bool = False) -> dict[str, Any]:
        """Counts, limited to the visible tiers. `withheld` is a single number, never a breakdown."""
        vis_sql, vis_params = self._visibility(include_sensitive)
        c = self.db.conn()
        out: dict[str, Any] = {}
        out["total"] = c.execute(f"SELECT count(*) AS n FROM memories WHERE {vis_sql}", vis_params).fetchone()["n"]
        out["by_type"] = {
            r["type"]: r["n"]
            for r in c.execute(
                f"SELECT type, count(*) AS n FROM memories WHERE {vis_sql} GROUP BY type ORDER BY n DESC",
                vis_params,
            ).fetchall()
        }
        out["updated_last_24h"] = c.execute(
            f"SELECT count(*) AS n FROM memories WHERE updated_at > now() - interval '24 hours' AND {vis_sql}",
            vis_params,
        ).fetchone()["n"]
        out["top_projects"] = [
            {"project_id": r["project_id"], "n": r["n"]}
            for r in c.execute(
                f"""SELECT project_id, count(*) AS n FROM memories
                    WHERE project_id IS NOT NULL AND {vis_sql}
                    GROUP BY project_id ORDER BY n DESC LIMIT 10""",
                vis_params,
            ).fetchall()
        ]
        if self._allowed(include_sensitive):
            out["by_sensitivity"] = {
                r["sensitivity"]: r["n"]
                for r in c.execute(
                    "SELECT sensitivity, count(*) AS n FROM memories GROUP BY sensitivity"
                ).fetchall()
            }
            out["withheld"] = 0
        else:
            out["withheld"] = c.execute(
                "SELECT count(*) AS n FROM memories WHERE NOT (sensitivity = ANY(%s))",
                [list(self.policy.default_visible)],
            ).fetchone()["n"]
        return self._tag(out, include_sensitive)

    # ---- write side (used by the indexer) ----------------------------------------

    def stored_hash(self, node_id: str) -> str | None:
        row = self.db.conn().execute(
            "SELECT content_hash FROM memories WHERE id = %s", (node_id,)
        ).fetchone()
        return row["content_hash"] if row else None

    def upsert(
        self, note: Note, chash: str, summary_emb: list[float], body_emb: list[float]
    ) -> None:
        self.db.conn().execute(
            """
            INSERT INTO memories (
                id, type, project_id, title, summary, body, sensitivity,
                summary_embedding, body_embedding, content_hash, source_file,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s::vector, %s::vector, %s, %s,
                COALESCE(%s::timestamptz, now()), COALESCE(%s::timestamptz, now())
            )
            ON CONFLICT (id) DO UPDATE SET
                type = EXCLUDED.type, project_id = EXCLUDED.project_id,
                title = EXCLUDED.title, summary = EXCLUDED.summary, body = EXCLUDED.body,
                sensitivity = EXCLUDED.sensitivity,
                summary_embedding = EXCLUDED.summary_embedding,
                body_embedding = EXCLUDED.body_embedding,
                content_hash = EXCLUDED.content_hash, source_file = EXCLUDED.source_file,
                updated_at = COALESCE(EXCLUDED.updated_at, now())
            """,
            (
                note.node_id, note.ntype, note.project_id, note.title, note.summary, note.body,
                note.sensitivity, vec_literal(summary_emb), vec_literal(body_emb), chash,
                note.source_file, note.created, note.updated,
            ),
        )

    def update_scalars(self, note: Note, chash: str) -> None:
        """Update every column except the embeddings. Used when the content hash is unchanged.

        sensitivity MUST be written on this path. A re-label is a frontmatter-only edit, so it
        leaves the content hash alone and arrives here and nowhere else. Leaving it out would
        make re-labelling a note silently do nothing, the worst failure this column can have.
        """
        self.db.conn().execute(
            """
            UPDATE memories SET
                type = %s, project_id = %s, title = %s, summary = %s, body = %s,
                sensitivity = %s, content_hash = %s, source_file = %s,
                updated_at = COALESCE(%s::timestamptz, now())
            WHERE id = %s
            """,
            (
                note.ntype, note.project_id, note.title, note.summary, note.body,
                note.sensitivity, chash, note.source_file, note.updated, note.node_id,
            ),
        )

    def delete_file(self, filename: str, stem: str) -> int:
        """Remove the row for a deleted file, by source file first and by id as a fallback."""
        cur = self.db.conn().execute(
            "DELETE FROM memories WHERE source_file = %s OR (id = %s AND source_file IS NULL)",
            (filename, stem),
        )
        return cur.rowcount

    def drop_stale_for_file(self, filename: str, keep_id: str) -> int:
        """If a file's frontmatter id changed, the row under the old id must not linger."""
        cur = self.db.conn().execute(
            "DELETE FROM memories WHERE source_file = %s AND id <> %s", (filename, keep_id)
        )
        return cur.rowcount
