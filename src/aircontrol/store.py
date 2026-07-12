from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from aircontrol.trajectory import Trajectory, deserialize, serialize


_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class GestureRecord:
    id: int
    name: str
    description: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class MappingRecord:
    id: int
    gesture_id: int
    context: str
    action: dict[str, Any]
    enabled: bool
    created_at: float


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    id: int
    name: str
    payload: dict[str, Any]
    active: bool


@dataclass(frozen=True, slots=True)
class GestureStatsRecord:
    gesture_id: int
    confirms: int
    rejects: int
    threshold_offset: float
    updated_at: float


def _gesture_record(row: sqlite3.Row) -> GestureRecord:
    return GestureRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        description=str(row["description"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _mapping_record(row: sqlite3.Row) -> MappingRecord:
    action = json.loads(str(row["action"]))
    if not isinstance(action, dict):
        raise ValueError("Stored mapping action must be a JSON object")
    return MappingRecord(
        id=int(row["id"]),
        gesture_id=int(row["gesture_id"]),
        context=str(row["context"]),
        action=action,
        enabled=bool(row["enabled"]),
        created_at=float(row["created_at"]),
    )


def _calibration_record(row: sqlite3.Row) -> CalibrationRecord:
    payload = json.loads(str(row["payload"]))
    if not isinstance(payload, dict):
        raise ValueError("Stored calibration payload must be a JSON object")
    return CalibrationRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        payload=payload,
        active=bool(row["active"]),
    )


def _gesture_stats_record(row: sqlite3.Row) -> GestureStatsRecord:
    return GestureStatsRecord(
        gesture_id=int(row["gesture_id"]),
        confirms=int(row["confirms"]),
        rejects=int(row["rejects"]),
        threshold_offset=float(row["threshold_offset"]),
        updated_at=float(row["updated_at"]),
    )


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class GestureRepo:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, name: str, description: str = "") -> GestureRecord:
        now = time.time()
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO gestures (name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, description, now, now),
            )
        return GestureRecord(
            id=int(cursor.lastrowid),
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )

    def get(self, id: int) -> GestureRecord | None:
        row = self._connection.execute(
            """
            SELECT id, name, description, created_at, updated_at
            FROM gestures
            WHERE id = ?
            """,
            (id,),
        ).fetchone()
        if row is None:
            return None
        return _gesture_record(row)

    def list(self) -> list[GestureRecord]:
        rows = self._connection.execute(
            """
            SELECT id, name, description, created_at, updated_at
            FROM gestures
            ORDER BY id
            """
        ).fetchall()
        return [_gesture_record(row) for row in rows]

    def rename(self, id: int, name: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE gestures SET name = ?, updated_at = ? WHERE id = ?",
                (name, time.time(), id),
            )

    def delete(self, id: int) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM gestures WHERE id = ?", (id,))


class ExemplarRepo:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, gesture_id: int, traj: Trajectory) -> int:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO exemplars (
                    gesture_id, trajectory, frame_count, created_at, handedness
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    gesture_id,
                    serialize(traj),
                    len(traj.frames),
                    time.time(),
                    traj.handedness,
                ),
            )
        return int(cursor.lastrowid)

    def list(self, gesture_id: int) -> list[Trajectory]:
        rows = self._connection.execute(
            """
            SELECT trajectory, handedness
            FROM exemplars
            WHERE gesture_id = ?
            ORDER BY id
            """,
            (gesture_id,),
        ).fetchall()
        return [
            deserialize(bytes(row["trajectory"]), str(row["handedness"]))
            for row in rows
        ]

    def list_with_ids(self, gesture_id: int) -> list[tuple[int, Trajectory]]:
        rows = self._connection.execute(
            """
            SELECT id, trajectory, handedness
            FROM exemplars
            WHERE gesture_id = ?
            ORDER BY id
            """,
            (gesture_id,),
        ).fetchall()
        return [
            (
                int(row["id"]),
                deserialize(bytes(row["trajectory"]), str(row["handedness"])),
            )
            for row in rows
        ]

    def count(self, gesture_id: int) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM exemplars WHERE gesture_id = ?",
            (gesture_id,),
        ).fetchone()
        return int(row[0])

    def delete(self, id: int) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM exemplars WHERE id = ?", (id,))


class MappingRepo:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def set(
        self,
        gesture_id: int,
        action: dict[str, Any],
        context: str = "global",
        enabled: bool = True,
    ) -> MappingRecord:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO mappings (
                    gesture_id, context, action, enabled, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(gesture_id, context) DO UPDATE SET
                    action = excluded.action,
                    enabled = excluded.enabled
                """,
                (
                    gesture_id,
                    context,
                    _json_text(action),
                    int(enabled),
                    time.time(),
                ),
            )
        record = self.for_gesture(gesture_id, context)
        if record is None:
            raise RuntimeError("Mapping upsert did not produce a record")
        return record

    def list(self, context: str = "global") -> list[MappingRecord]:
        rows = self._connection.execute(
            """
            SELECT id, gesture_id, context, action, enabled, created_at
            FROM mappings
            WHERE context = ?
            ORDER BY id
            """,
            (context,),
        ).fetchall()
        return [_mapping_record(row) for row in rows]

    def for_gesture(
        self,
        gesture_id: int,
        context: str = "global",
    ) -> MappingRecord | None:
        row = self._connection.execute(
            """
            SELECT id, gesture_id, context, action, enabled, created_at
            FROM mappings
            WHERE gesture_id = ? AND context = ?
            """,
            (gesture_id, context),
        ).fetchone()
        if row is None:
            return None
        return _mapping_record(row)


class CalibrationRepo:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(
        self,
        name: str,
        payload: dict[str, Any],
        active: bool = False,
    ) -> CalibrationRecord:
        with self._connection:
            if active:
                self._connection.execute(
                    "UPDATE calibration_profiles SET active = 0 WHERE active != 0"
                )
            cursor = self._connection.execute(
                """
                INSERT INTO calibration_profiles (
                    name, payload, active, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (name, _json_text(payload), int(active), time.time()),
            )
        return CalibrationRecord(
            id=int(cursor.lastrowid),
            name=name,
            payload=json.loads(_json_text(payload)),
            active=active,
        )

    def active(self) -> CalibrationRecord | None:
        row = self._connection.execute(
            """
            SELECT id, name, payload, active
            FROM calibration_profiles
            WHERE active != 0
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return _calibration_record(row)

    def list(self) -> list[CalibrationRecord]:
        rows = self._connection.execute(
            """
            SELECT id, name, payload, active
            FROM calibration_profiles
            ORDER BY id
            """
        ).fetchall()
        return [_calibration_record(row) for row in rows]


class GestureStatsRepo:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, gesture_id: int) -> GestureStatsRecord:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO gesture_stats (gesture_id, updated_at)
                VALUES (?, ?)
                ON CONFLICT(gesture_id) DO NOTHING
                """,
                (gesture_id, time.time()),
            )
        row = self._connection.execute(
            """
            SELECT gesture_id, confirms, rejects, threshold_offset, updated_at
            FROM gesture_stats
            WHERE gesture_id = ?
            """,
            (gesture_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Gesture stats row was not created")
        return _gesture_stats_record(row)

    def record_confirm(self, gesture_id: int) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO gesture_stats (gesture_id, confirms, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(gesture_id) DO UPDATE SET
                    confirms = gesture_stats.confirms + 1,
                    updated_at = excluded.updated_at
                """,
                (gesture_id, time.time()),
            )

    def record_reject(
        self,
        gesture_id: int,
        offset_bump: float = 0.02,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO gesture_stats (
                    gesture_id, rejects, threshold_offset, updated_at
                )
                VALUES (?, 1, ?, ?)
                ON CONFLICT(gesture_id) DO UPDATE SET
                    rejects = gesture_stats.rejects + 1,
                    threshold_offset = (
                        gesture_stats.threshold_offset
                        + excluded.threshold_offset
                    ),
                    updated_at = excluded.updated_at
                """,
                (gesture_id, offset_bump, time.time()),
            )

    def reset(self, gesture_id: int) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO gesture_stats (
                    gesture_id, confirms, rejects, threshold_offset, updated_at
                )
                VALUES (?, 0, 0, 0.0, ?)
                ON CONFLICT(gesture_id) DO UPDATE SET
                    confirms = 0,
                    rejects = 0,
                    threshold_offset = 0.0,
                    updated_at = excluded.updated_at
                """,
                (gesture_id, time.time()),
            )


class Store:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

        self.gestures = GestureRepo(self._connection)
        self.exemplars = ExemplarRepo(self._connection)
        self.mappings = MappingRepo(self._connection)
        self.calibration = CalibrationRepo(self._connection)
        self.gesture_stats = GestureStatsRepo(self._connection)

    @property
    def schema_version(self) -> int:
        row = self._connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        if row is None:
            raise RuntimeError("Database schema version is missing")
        return int(row["version"])

    def delete_everything(self) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM gesture_stats")
            self._connection.execute("DELETE FROM exemplars")
            self._connection.execute("DELETE FROM mappings")
            self._connection.execute("DELETE FROM gestures")
            self._connection.execute("DELETE FROM calibration_profiles")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _migrate(self) -> None:
        with self._connection:
            version_table_exists = self._connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_version'
                """
            ).fetchone()

            if version_table_exists is None:
                self._connection.execute(
                    "CREATE TABLE schema_version (version INTEGER NOT NULL)"
                )
                current_version = 0
            else:
                rows = self._connection.execute(
                    "SELECT version FROM schema_version"
                ).fetchall()
                if len(rows) > 1:
                    raise RuntimeError("Database has multiple schema version rows")
                current_version = int(rows[0]["version"]) if rows else 0

            if current_version > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {current_version} is newer than "
                    f"supported version {_SCHEMA_VERSION}"
                )

            if current_version < 1:
                self._connection.execute(
                    """
                    CREATE TABLE gestures (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        description TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE exemplars (
                        id INTEGER PRIMARY KEY,
                        gesture_id INTEGER NOT NULL,
                        trajectory BLOB NOT NULL,
                        frame_count INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        handedness TEXT,
                        FOREIGN KEY (gesture_id) REFERENCES gestures(id)
                            ON DELETE CASCADE
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE mappings (
                        id INTEGER PRIMARY KEY,
                        gesture_id INTEGER NOT NULL,
                        context TEXT NOT NULL DEFAULT 'global',
                        action TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at REAL NOT NULL,
                        UNIQUE(gesture_id, context),
                        FOREIGN KEY (gesture_id) REFERENCES gestures(id)
                            ON DELETE CASCADE
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE calibration_profiles (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL
                    )
                    """
                )
                self._connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (1,),
                )
                current_version = 1

            if current_version < 2:
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS gesture_stats (
                        gesture_id INTEGER PRIMARY KEY
                            REFERENCES gestures(id) ON DELETE CASCADE,
                        confirms INTEGER NOT NULL DEFAULT 0,
                        rejects INTEGER NOT NULL DEFAULT 0,
                        threshold_offset REAL NOT NULL DEFAULT 0.0,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                self._connection.execute(
                    "UPDATE schema_version SET version = ?",
                    (_SCHEMA_VERSION,),
                )
