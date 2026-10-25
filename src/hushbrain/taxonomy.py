"""Node and edge types for the typed-note vault.

The 16 node types and 10 edge types follow the "infinite brain" approach described by
AI Impact (see the README credits). `project` is a local extension: it lets a note name
the project it belongs to without being a pillar.
"""
from __future__ import annotations

NODE_TYPES = frozenset({
    "pillar", "decision", "concept", "question", "playbook", "task",
    "event", "pattern", "hypothesis", "fact", "source", "bookmark",
    "note", "contact", "reference", "custom",
    "project",
})

EDGE_TYPES = frozenset({
    "supports", "contradicts", "depends_on", "derived_from", "related_to",
    "part_of", "preceded_by", "followed_by", "authored_by", "tag",
})
