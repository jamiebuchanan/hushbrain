"""The MCP server: thin tool wrappers over MemoryStore.

The discipline this server enforces on its caller:

* search, cross_project, recent, project_context and what_changed_since return one-line
  summaries, never bodies. Read many summaries cheaply, then decide what is worth reading.
* get_body is the only way to read a full note, and it is a separate call on one id.
* Every tool excludes personal and legal notes unless the caller passes include_sensitive.
"""
from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from hushbrain.store import MemoryStore

# The opt-in flag is a strict boolean at the tool boundary. Without strict=True, pydantic's
# lax mode turns the string "true", "yes", "1" and the integer 1 into True, so a model could
# opt in with a value that a host-side hook matching on `include_sensitive: true` would not
# see. With it, anything but a JSON boolean is rejected with an error and nothing is read.
SensitiveFlag = Annotated[bool, Field(strict=True)]


def build_server(store: MemoryStore) -> FastMCP:
    mcp = FastMCP("hushbrain")

    @mcp.tool()
    def search(
        query: str,
        limit: int | None = None,
        type: str | None = None,
        project_id: str | None = None,
        include_sensitive: SensitiveFlag = False,
    ) -> dict[str, Any]:
        """Semantic search over note summaries. Returns candidates with id, type, title, a
        one-line summary, project_id and a similarity score. Never returns bodies: call
        get_body(id) for the few notes worth reading in full.

        Use this first when the user asks a question. By default only notes in the visible
        tiers (normally just 'normal') are returned; personal and legal notes are excluded so
        they cannot drift into unrelated work. Pass include_sensitive=true ONLY when the user
        has said this conversation is about their private life.

        Args:
            query: natural-language question or keywords
            limit: max results (server default and cap apply)
            type: optional filter to one node type
            project_id: optional filter to one project
            include_sensitive: include personal and legal notes (default false)
        """
        return store.search(query, limit, type, project_id, include_sensitive)

    @mcp.tool()
    def cross_project(
        query: str, limit: int | None = None, include_sensitive: SensitiveFlag = False
    ) -> dict[str, Any]:
        """Search across every project. Dropping the project filter never drops the
        sensitivity filter: personal and legal notes stay excluded unless include_sensitive
        is true.
        """
        return store.cross_project(query, limit, include_sensitive)

    @mcp.tool()
    def recent(
        limit: int | None = None, type: str | None = None, include_sensitive: SensitiveFlag = False
    ) -> dict[str, Any]:
        """The most recently updated notes, newest first. Cheap: no embedding is computed.
        Excludes personal and legal notes by default.
        """
        return store.recent(limit, type, include_sensitive)

    @mcp.tool()
    def project_context(
        project_id: str, limit: int | None = None, include_sensitive: SensitiveFlag = False
    ) -> dict[str, Any]:
        """One project's pillar, decisions, questions and tasks first, then its other notes.
        Summaries only. Excludes personal and legal notes by default, so asking for a private
        project by name still returns nothing without include_sensitive.
        """
        return store.project_context(project_id, limit, include_sensitive)

    @mcp.tool()
    def what_changed_since(since_iso: str, include_sensitive: SensitiveFlag = False) -> dict[str, Any]:
        """Notes updated after an ISO 8601 timestamp, newest first. Use to catch up after a
        context reset. Excludes personal and legal notes by default.
        """
        return store.what_changed_since(since_iso, include_sensitive)

    @mcp.tool()
    def get_body(id: str, include_sensitive: SensitiveFlag = False) -> dict[str, Any]:
        """Fetch the full text of one note by id. Only call this after a search or recent call
        has surfaced the id and the note is worth the tokens.

        Refuses personal and legal notes unless include_sensitive is true. The refusal names
        the tier and returns no content, so the caller learns the note exists and that an
        explicit opt-in is needed.
        """
        return store.get_body(id, include_sensitive)

    @mcp.tool()
    def stats(include_sensitive: SensitiveFlag = False) -> dict[str, Any]:
        """Counts by type, recent activity and top projects, limited to the visible tiers.
        `withheld` says how many notes are hidden, without saying what they are.
        """
        return store.stats(include_sensitive)

    return mcp
