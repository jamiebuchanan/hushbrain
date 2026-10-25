"""Keeps the database in step with a vault of markdown notes.

  markdown vault (source of truth)
      -> parse frontmatter -> resolve the sensitivity tier through the policy
      -> embed summary and body -> upsert into Postgres + pgvector

No LLM is called at index time. Frontmatter is canonical and nothing is inferred, so
indexing has no per-note cost.

Two things here exist because file watchers cannot be trusted:

* A reconciliation sweep (cheap mtime scan) runs alongside the watcher and catches whatever
  the watcher drops. Each file the sweep has to pick up is logged as a WATCHER MISS, so a
  broken watcher stays visible instead of being papered over.
* The watch is recursive. On macOS, FSEvents coalesces events on a large directory into one
  directory-level event, and a non-recursive watcher discards it, so every change is lost.
  The vault is flat, so recursion costs nothing. Do not "tidy" it back.

Logging: note ids can themselves be private (a title is often enough to disclose), so notes
outside the default-visible tiers are logged under a short hash, never by name.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from hushbrain.config import Settings
from hushbrain.embeddings import Embedder
from hushbrain.notes import content_hash, parse_note, safe_name
from hushbrain.policy import Policy
from hushbrain.store import MemoryStore

log = logging.getLogger("hushbrain.indexer")


@dataclass
class ScanResult:
    files: int = 0
    embedded: int = 0
    skipped_embeds: int = 0
    failed: int = 0


class Indexer:
    def __init__(
        self, settings: Settings, policy: Policy, store: MemoryStore, embedder: Embedder
    ) -> None:
        self.settings = settings
        self.policy = policy
        self.store = store
        self.embedder = embedder
        self.embedded = 0
        self.skipped_embeds = 0
        self.failed = 0
        self._pending: dict[Path, float] = {}
        self._lock = threading.Lock()
        # Seeded with now(), not 0. It is compared with file mtimes, so a zero seed would make
        # the first sweep re-index every file straight after the initial scan did the same.
        # time.time(), not monotonic(): mtimes are epoch-based.
        self._last_reconcile = time.time()
        self._seen_files: set[Path] = set()
        # mtime at the moment each file was last handled, keyed on the FILENAME. The watcher
        # and glob() can disagree on the casing of the parent directory (case-insensitive
        # volumes), so a dict keyed on Path would never match across the two sources.
        self._handled_mtime: dict[str, float] = {}

    # ---- logging -------------------------------------------------------------------

    def _label(self, name: str, tier: str | None) -> str:
        return name if tier in self.policy.default_visible else safe_name(name)

    # ---- one file ------------------------------------------------------------------

    def index_file(self, path: Path) -> str:
        """Index, refresh or delete one file. Returns indexed, refreshed, deleted or failed.

        "refreshed" means the content hash was unchanged, so both embeddings were skipped and
        only the scalar columns (including the sensitivity tier) were rewritten.
        """
        if not path.exists():
            self.delete_file(path)
            return "deleted"
        note = parse_note(path, self.policy)
        if note is None:
            self.failed += 1
            return "failed"
        label = self._label(note.node_id, note.sensitivity)
        try:
            chash = content_hash(note.summary, note.body)
            if self.store.stored_hash(note.node_id) == chash:
                # Summary and body unchanged: skip both embeddings, still write every scalar.
                self.store.update_scalars(note, chash)
                self.skipped_embeds += 1
                outcome = "refreshed"
            else:
                self.store.upsert(
                    note, chash,
                    self.embedder.embed(note.summary),
                    self.embedder.embed(note.body or note.summary),
                )
                self.embedded += 1
                outcome = "indexed"
            self.store.drop_stale_for_file(note.source_file, note.node_id)
        except Exception as e:  # noqa: BLE001 - one bad note must not stop the run
            self.failed += 1
            log.error("index failed for %s: %s", label, type(e).__name__)
            return "failed"
        if note.sensitivity_reason in {"missing", "invalid"}:
            log.warning(
                "%s has no valid sensitivity label (%s); stored as '%s'",
                label, note.sensitivity_reason, note.sensitivity,
            )
        log.info("%s %s (%s, %s)", outcome, label, note.ntype, note.sensitivity)
        return outcome

    def delete_file(self, path: Path) -> None:
        try:
            n = self.store.delete_file(path.name, path.stem)
            log.info("deleted %s (%d row)", safe_name(path.name), n)
        except Exception as e:  # noqa: BLE001
            log.error("delete failed for %s: %s", safe_name(path.name), type(e).__name__)

    # ---- bulk ----------------------------------------------------------------------

    def initial_scan(self) -> ScanResult:
        nodes_dir = self.settings.nodes_dir
        if not nodes_dir.exists():
            log.warning("nodes dir %s does not exist; skipping the initial scan", nodes_dir)
            return ScanResult()
        files = sorted(p for p in nodes_dir.glob("*.md") if p.is_file() and not p.name.startswith("."))
        before = (self.embedded, self.skipped_embeds, self.failed)
        for p in files:
            self.index_file(p)
        res = ScanResult(
            files=len(files),
            embedded=self.embedded - before[0],
            skipped_embeds=self.skipped_embeds - before[1],
            failed=self.failed - before[2],
        )
        log.info(
            "initial scan: %d files, embedded %d, unchanged %d, failed %d",
            res.files, res.embedded, res.skipped_embeds, res.failed,
        )
        return res

    # ---- watching ------------------------------------------------------------------

    def schedule(self, path: Path) -> None:
        with self._lock:
            self._pending[path] = time.monotonic() + self.settings.debounce_ms / 1000.0

    def drain(self) -> int:
        now = time.monotonic()
        ready: list[Path] = []
        with self._lock:
            for p, due in list(self._pending.items()):
                if now >= due:
                    ready.append(p)
                    del self._pending[p]
        for p in ready:
            # Record the mtime BEFORE indexing: if the file is rewritten while we work, the
            # stored value stays older than the new mtime and the next sweep picks it up.
            try:
                self._handled_mtime[p.name] = p.stat().st_mtime
            except OSError:
                self._handled_mtime.pop(p.name, None)
            self.index_file(p)
        return len(ready)

    def reconcile(self) -> list[Path]:
        """Safety net for a watcher that cannot be trusted. Returns the files the watcher missed.

        Scans for files newer than the last sweep and schedules them. Deletions are handled
        only for files this process has seen, so a transiently unreadable directory can never
        mass-delete rows.
        """
        cutoff = self._last_reconcile
        newest = cutoff
        missed: list[Path] = []
        try:
            entries = list(self.settings.nodes_dir.glob("*.md"))
        except OSError as e:
            log.warning("reconcile: cannot read the nodes dir: %s", type(e).__name__)
            return missed

        seen_now: set[Path] = set()
        for p in entries:
            if p.name.startswith("."):
                continue  # AppleDouble artefacts from network mounts
            seen_now.add(p)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            newest = max(newest, mtime)
            if mtime > cutoff:
                handled = self._handled_mtime.get(p.name)
                with self._lock:
                    already = p in self._pending
                if not already and (handled is None or mtime > handled):
                    missed.append(p)
                self._handled_mtime[p.name] = mtime
                self.schedule(p)

        gone = self._seen_files - seen_now if self._seen_files else set()
        self._seen_files = seen_now
        self._last_reconcile = max(newest, cutoff)

        if missed:
            log.warning(
                "reconcile: WATCHER MISS, %d file(s) changed without a filesystem event; the sweep caught them",
                len(missed),
            )
        for p in gone:
            log.info("reconcile: %s removed", safe_name(p.name))
            self.delete_file(p)
        return missed


class _Handler(FileSystemEventHandler):
    def __init__(self, indexer: Indexer) -> None:
        self.indexer = indexer

    @staticmethod
    def _is_md(path: Any) -> bool:
        p = str(path)
        return p.endswith(".md") and not Path(p).name.startswith(".")

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_md(event.src_path):
            self.indexer.schedule(Path(str(event.src_path)))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_md(event.src_path):
            self.indexer.schedule(Path(str(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_md(event.src_path):
            self.indexer.delete_file(Path(str(event.src_path)))
        if self._is_md(event.dest_path):
            self.indexer.schedule(Path(str(event.dest_path)))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_md(event.src_path):
            self.indexer.delete_file(Path(str(event.src_path)))


def watch(indexer: Indexer) -> int:
    """Run until SIGINT or SIGTERM: initial scan, then watcher plus reconciliation sweep."""
    s = indexer.settings
    nodes_dir = s.nodes_dir
    nodes_dir.mkdir(parents=True, exist_ok=True)

    stop = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:
        log.info("signal %d received, stopping", signum)
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    log.info("watching %s", nodes_dir)
    if s.initial_scan:
        indexer.initial_scan()

    observer = Observer()
    observer.schedule(_Handler(indexer), str(nodes_dir), recursive=True)
    observer.start()
    next_reconcile = time.monotonic() + s.reconcile_sec
    try:
        while not stop.wait(0.2):
            indexer.drain()
            if s.reconcile_sec > 0 and time.monotonic() >= next_reconcile:
                indexer.reconcile()
                next_reconcile = time.monotonic() + s.reconcile_sec
    finally:
        observer.stop()
        observer.join(timeout=5)
        indexer.store.db.close()
        log.info("indexer stopped")
    return 0
