"""The sensitivity policy: one small TOML file that every component reads.

Why it exists: tier names, what is visible by default, and what an unlabelled note becomes
used to be scattered across the server, the indexer and ad hoc scripts, and drifted apart.
One file, one loader, and a validator that refuses a policy that would fail open.

Three sections matter:

  [tiers]    levels (least to most restricted, first must be "normal"), default_visible,
             default_for_missing (the tier an unlabelled or unrecognised note receives).
  [projects] tier floors: a note in a listed project can never be less restricted than the
             floor, whatever its own label says. Labels can only go up, never down.
  [export]   an egress gate for anything that leaves the machine (bundles, replicas, posts).
             Default deny: a note must pass the tier, type, project and blocklist rules.

Fail closed: an unreadable or invalid policy raises PolicyError. Components that can degrade
safely (the server, the indexer) call load_or_builtin(), which falls back to the strictest
built-in policy and logs the reason, instead of serving more than the policy intended.

THIS IS NOT ACCESS CONTROL. It keeps private material out of default retrieval so it does not
drift into a client email or a commit by accident. Anyone who can read the database reads
everything in it.
"""
from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_VERSION = 1
log = logging.getLogger("hushbrain.policy")


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportRules:
    allow_tiers: tuple[str, ...] = ("normal",)
    allow_types: tuple[str, ...] = ()
    deny_types: tuple[str, ...] = ()
    deny_projects: tuple[str, ...] = ()
    scan_fields: tuple[str, ...] = ("id", "title", "summary", "body")
    blocklist: re.Pattern[str] | None = None


@dataclass(frozen=True)
class Policy:
    levels: tuple[str, ...] = ("normal", "personal", "legal")
    default_visible: tuple[str, ...] = ("normal",)
    default_for_missing: str = "personal"
    project_floors: Mapping[str, str] = field(default_factory=dict)
    export: ExportRules = field(default_factory=ExportRules)
    source: str = "builtin"

    # ---- labels ----------------------------------------------------------------

    def resolve_tier(self, raw: Any) -> tuple[str, str]:
        """Turn a note's own label into a stored tier. Returns (tier, reason).

        reason is "explicit", "missing" or "invalid". Missing and invalid labels both
        become default_for_missing: an omission is as likely to be an oversight on
        something private as a typo is, and over-labelling costs a note missing from a
        default search, where under-labelling costs private text in the wrong place.
        """
        if raw is None or not str(raw).strip():
            return self.default_for_missing, "missing"
        tier = str(raw).strip().lower()
        if tier in self.levels:
            return tier, "explicit"
        return self.default_for_missing, "invalid"

    def apply_project_floor(self, tier: str, project_id: str | None) -> tuple[str, bool]:
        """Raise tier to the project's floor if it is below it. Returns (tier, raised)."""
        floor = self.project_floors.get((project_id or "").lower())
        if floor and self.levels.index(floor) > self.levels.index(tier):
            return floor, True
        return tier, False

    # ---- reads -----------------------------------------------------------------

    def visible_tiers(self, include_sensitive: bool) -> tuple[str, ...] | None:
        """Tiers a read may return, or None for all. Anything but a literal True is False."""
        if include_sensitive is True:
            return None
        return self.default_visible

    # ---- egress ----------------------------------------------------------------

    def export_decision(self, node: Mapping[str, Any]) -> tuple[bool, str]:
        """Admit or refuse one node for export. Default deny.

        node keys: sensitivity (required) and any of id, type, project_id, title, summary,
        body. Only fields present are scanned, so a caller that must not read bodies simply
        leaves them out.
        """
        r = self.export
        if node.get("sensitivity") not in r.allow_tiers:
            return False, "tier"
        if node.get("type") in r.deny_types:
            return False, "type"
        if (node.get("project_id") or "").lower() in r.deny_projects:
            return False, "project"
        if r.allow_types and node.get("type") not in r.allow_types:
            return False, "type-not-allowed"
        if r.blocklist is not None:
            for f in r.scan_fields:
                v = node.get(f)
                if v and r.blocklist.search(str(v)):
                    return False, f"blocklist:{f}"
        return True, "ok"


# ---- loading ---------------------------------------------------------------------


def _flex(term: str) -> str:
    """A term that matches across spaces, underscores, hyphens and apostrophes."""
    toks = [t for t in re.split(r"[\s_'-]+", term.strip()) if t]
    return r"[\s_'-]*".join(re.escape(t) for t in toks)


def _str_list(section: Mapping[str, Any], key: str, where: str) -> list[str]:
    v = section.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise PolicyError(f"{where}.{key} must be a list of strings")
    return [x.strip() for x in v if x.strip()]


def parse(d: Mapping[str, Any], source: str = "inline") -> Policy:
    if d.get("version") != SUPPORTED_VERSION:
        raise PolicyError(f"policy version {d.get('version')!r} unsupported (want {SUPPORTED_VERSION})")

    t = d.get("tiers") or {}
    levels = _str_list(t, "levels", "tiers")
    levels = [x.lower() for x in levels]
    if not levels or levels[0] != "normal":
        raise PolicyError("tiers.levels must start with 'normal'")
    if len(set(levels)) != len(levels):
        raise PolicyError("tiers.levels has duplicates")
    visible = [x.lower() for x in _str_list(t, "default_visible", "tiers")]
    if not set(visible) <= set(levels):
        raise PolicyError("tiers.default_visible is not a subset of tiers.levels")
    missing = str(t.get("default_for_missing", "")).strip().lower()
    if missing not in levels or missing == "normal":
        raise PolicyError("tiers.default_for_missing must be a non-normal tier in tiers.levels (fail closed)")
    if missing in visible:
        raise PolicyError("tiers.default_for_missing must not be in tiers.default_visible")

    floors: dict[str, str] = {}
    projects = d.get("projects") or {}
    for tier in projects:
        tier_l = tier.lower()
        if tier_l not in levels or tier_l == "normal":
            raise PolicyError(f"projects.{tier}: not a non-normal tier in tiers.levels")
        for pid in _str_list(projects, tier, "projects"):
            key = pid.lower()
            # If a project is listed under two tiers, the more restricted one wins.
            if key not in floors or levels.index(tier_l) > levels.index(floors[key]):
                floors[key] = tier_l

    e = d.get("export") or {}
    allow_tiers = tuple(x.lower() for x in _str_list(e, "allow_tiers", "export")) or ("normal",)
    if not set(allow_tiers) <= set(levels):
        raise PolicyError("export.allow_tiers is not a subset of tiers.levels")
    parts = [_flex(x) for x in _str_list(e, "blocklist_terms", "export")]
    parts += [
        r"(?<![A-Za-z0-9])" + _flex(x) + r"(?![A-Za-z0-9])"
        for x in _str_list(e, "blocklist_word_terms", "export")
    ]
    parts += _str_list(e, "blocklist_regex", "export")
    blocklist = None
    if parts:
        try:
            blocklist = re.compile("|".join(f"(?:{p})" for p in parts), re.I)
        except re.error as err:
            raise PolicyError(f"export blocklist does not compile: {err}") from err

    return Policy(
        levels=tuple(levels),
        default_visible=tuple(visible),
        default_for_missing=missing,
        project_floors=floors,
        export=ExportRules(
            allow_tiers=allow_tiers,
            allow_types=tuple(_str_list(e, "allow_types", "export")),
            deny_types=tuple(_str_list(e, "deny_types", "export")),
            deny_projects=tuple(x.lower() for x in _str_list(e, "deny_projects", "export")),
            scan_fields=tuple(_str_list(e, "scan_fields", "export")) or ExportRules().scan_fields,
            blocklist=blocklist,
        ),
        source=source,
    )


def load(path: Path | str) -> Policy:
    p = Path(path).expanduser()
    try:
        with open(p, "rb") as f:
            d = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise PolicyError(f"cannot load {p}: {e}") from e
    return parse(d, source=str(p))


def builtin() -> Policy:
    """The strictest sensible policy: three tiers, only 'normal' visible, unlabelled is private."""
    return Policy()


def load_or_builtin(path: Path | str | None) -> Policy:
    if path is None:
        log.warning("no policy file configured; using the built-in policy (normal is the only default tier)")
        return builtin()
    try:
        return load(path)
    except PolicyError as e:
        log.error("%s; falling back to the built-in policy (project floors and export rules are off)", e)
        return builtin()
