"""Single-host journal. The OS lock owns execution; SQLite owns committed state."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sqlite3
import threading


class DurableStore:
    TABLES = ("runs", "agents", "messages", "events")

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self.failed = False
        self.closed = False
        self._owner = open(str(self.path) + ".lock", "a+b")
        os.chmod(self._owner.name, 0o600)
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._owner.close()
            raise RuntimeError("Another Swarm coordinator owns this database") from None
        try:
            # Create privately before SQLite opens WAL/SHM siblings.
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
            self.db = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError(f"Unsupported Swarm journal schema {version}; data retained")
            with self.db:
                self.db.execute("CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER, revision INTEGER)")
                self.db.execute("INSERT OR IGNORE INTO meta VALUES(1, 0, 0)")
                for table in self.TABLES:
                    self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                self.db.execute("PRAGMA user_version=1")
                self.db.execute("UPDATE meta SET generation=generation+1 WHERE id=1")
            self.generation, self.revision = self.db.execute("SELECT generation, revision FROM meta").fetchone()
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self._owner.close()
            raise

    def load(self):
        with self._lock:
            return {table: {key: json.loads(payload) for key, payload in self.db.execute(f"SELECT id, payload FROM {table}")} for table in self.TABLES}

    def save(self, state):
        with self._lock:
            if self.failed or self.closed:
                raise RuntimeError("Swarm journal is unavailable; execution is held")
            try:
                with self.db:
                    changed = self.db.execute("UPDATE meta SET revision=revision+1 WHERE id=1 AND generation=?", (self.generation,))
                    if changed.rowcount != 1:
                        raise RuntimeError("Stale Swarm coordinator")
                    for table in self.TABLES:
                        self.db.execute(f"DELETE FROM {table}")
                        self.db.executemany(f"INSERT INTO {table} VALUES(?, ?)", [(key, json.dumps(value, ensure_ascii=False)) for key, value in state[table].items()])
                self.revision += 1
            except BaseException:
                self.failed = True
                raise

    def close(self):
        with self._lock:
            if not self.closed:
                self.closed = True
                self.db.close()
                self._owner.close()
