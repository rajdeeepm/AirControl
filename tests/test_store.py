from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from aircontrol.domain import Point3D
from aircontrol.store import (
    CalibrationRecord,
    CalibrationRepo,
    ExemplarRepo,
    GestureRecord,
    GestureRepo,
    MappingRecord,
    MappingRepo,
    Store,
)
from aircontrol.trajectory import LandmarkFrame, Trajectory, deserialize, serialize


def _trajectory(handedness: str = "Left") -> Trajectory:
    frames = []
    for frame_index, timestamp in enumerate((0.0, 0.125)):
        landmarks = tuple(
            Point3D(
                x=frame_index + landmark_index * 0.01,
                y=frame_index + landmark_index * 0.02,
                z=landmark_index * -0.005,
            )
            for landmark_index in range(21)
        )
        frames.append(
            LandmarkFrame(
                landmarks=landmarks,
                handedness=handedness,
                timestamp=timestamp,
            )
        )
    return Trajectory(frames=tuple(frames), handedness=handedness)


def test_fresh_database_has_schema_version_two(tmp_path):
    path = tmp_path / "aircontrol.db"

    with Store(path) as store:
        assert store.schema_version == 4
        assert isinstance(store.gestures, GestureRepo)
        assert isinstance(store.exemplars, ExemplarRepo)
        assert isinstance(store.mappings, MappingRepo)
        assert isinstance(store.calibration, CalibrationRepo)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        exemplar_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(exemplars)")
        }
        assert "handedness" in exemplar_columns


def test_migration_is_idempotent_when_database_is_reopened(tmp_path):
    path = tmp_path / "aircontrol.db"

    with Store(path) as store:
        gesture = store.gestures.add("Wave")

    with Store(path) as reopened:
        assert reopened.schema_version == 4
        assert reopened.gestures.get(gesture.id) == gesture

    with sqlite3.connect(path) as connection:
        versions = connection.execute("SELECT version FROM schema_version").fetchall()
    assert versions == [(4,)]


def test_gesture_crud(tmp_path):
    with Store(tmp_path / "aircontrol.db") as store:
        gesture = store.gestures.add("Wave", description="Open-palm wave")

        assert isinstance(gesture, GestureRecord)
        assert gesture.name == "Wave"
        assert gesture.description == "Open-palm wave"
        assert gesture.created_at == gesture.updated_at
        assert store.gestures.get(gesture.id) == gesture
        assert store.gestures.list() == [gesture]

        store.gestures.rename(gesture.id, "Hello")
        renamed = store.gestures.get(gesture.id)
        assert renamed is not None
        assert renamed.name == "Hello"
        assert renamed.description == gesture.description
        assert renamed.created_at == gesture.created_at
        assert renamed.updated_at >= gesture.updated_at

        store.gestures.delete(gesture.id)
        assert store.gestures.get(gesture.id) is None
        assert store.gestures.list() == []


def test_exemplar_add_and_list_round_trip_trajectory(tmp_path):
    trajectory = _trajectory("Left")
    expected = deserialize(serialize(trajectory), "Left")

    with Store(tmp_path / "aircontrol.db") as store:
        gesture = store.gestures.add("Swipe")
        exemplar_id = store.exemplars.add(gesture.id, trajectory)

        assert isinstance(exemplar_id, int)
        assert store.exemplars.count(gesture.id) == 1
        assert store.exemplars.list(gesture.id) == [expected]
        assert store.exemplars.list(gesture.id)[0].handedness == "Left"

        store.exemplars.delete(exemplar_id)
        assert store.exemplars.count(gesture.id) == 0
        assert store.exemplars.list(gesture.id) == []


def test_deleting_gesture_cascades_to_exemplars_and_mappings(tmp_path):
    with Store(tmp_path / "aircontrol.db") as store:
        gesture = store.gestures.add("Pinch")
        store.exemplars.add(gesture.id, _trajectory())
        store.mappings.set(gesture.id, {"type": "click"})

        store.gestures.delete(gesture.id)

        assert store.exemplars.count(gesture.id) == 0
        assert store.mappings.for_gesture(gesture.id) is None
        assert store.mappings.list() == []


def test_mapping_defaults_to_global_context_and_upserts(tmp_path):
    path = tmp_path / "aircontrol.db"

    with Store(path) as store:
        gesture = store.gestures.add("Swipe Right")
        original = store.mappings.set(
            gesture.id,
            {"type": "hotkey", "keys": ["alt", "right"]},
        )
        updated = store.mappings.set(
            gesture.id,
            {"type": "hotkey", "keys": ["ctrl", "tab"]},
            enabled=False,
        )

        assert isinstance(original, MappingRecord)
        assert original.context == "global"
        assert updated.id == original.id
        assert updated.context == "global"
        assert updated.action == {"type": "hotkey", "keys": ["ctrl", "tab"]}
        assert updated.enabled is False
        assert store.mappings.list() == [updated]
        assert store.mappings.for_gesture(gesture.id) == updated

    with sqlite3.connect(path) as connection:
        stored_action = connection.execute("SELECT action FROM mappings").fetchone()[0]
    assert isinstance(stored_action, str)
    assert json.loads(stored_action) == updated.action


def test_calibration_save_and_active_retrieval(tmp_path):
    path = tmp_path / "aircontrol.db"

    with Store(path) as store:
        inactive = store.calibration.save("Desk", {"scale": 1.0})
        assert isinstance(inactive, CalibrationRecord)
        assert inactive.active is False
        assert store.calibration.active() is None

        active = store.calibration.save(
            "Standing desk",
            {"scale": 1.25, "origin": [0.1, 0.2]},
            active=True,
        )
        assert active.active is True
        assert store.calibration.active() == active
        assert store.calibration.list() == [inactive, active]

    with sqlite3.connect(path) as connection:
        stored_payload = connection.execute(
            "SELECT payload FROM calibration_profiles WHERE id = ?",
            (active.id,),
        ).fetchone()[0]
    assert isinstance(stored_payload, str)
    assert json.loads(stored_payload) == active.payload


def test_delete_everything_empties_all_data_tables(tmp_path):
    with Store(tmp_path / "aircontrol.db") as store:
        gesture = store.gestures.add("Rotate")
        store.exemplars.add(gesture.id, _trajectory())
        store.mappings.set(gesture.id, {"type": "volume", "delta": 1})
        store.calibration.save("Default", {"scale": 1.0}, active=True)

        store.delete_everything()

        assert store.gestures.list() == []
        assert store.exemplars.count(gesture.id) == 0
        assert store.mappings.list() == []
        assert store.calibration.list() == []
        assert store.schema_version == 4


@pytest.mark.parametrize(
    ("record_type", "values"),
    [
        (GestureRecord, (1, "Wave", "", 1.0, 1.0)),
        (MappingRecord, (1, 1, "global", {"type": "click"}, True, 1.0)),
        (CalibrationRecord, (1, "Desk", {"scale": 1.0}, True)),
    ],
)
def test_record_types_are_frozen_and_slotted(record_type, values):
    record = record_type(*values)

    assert hasattr(record_type, "__slots__")
    with pytest.raises(FrozenInstanceError):
        record.id = 2


def test_fresh_database_reports_schema_version_two(tmp_path):
    with Store(tmp_path / "aircontrol.db") as store:
        assert store.schema_version == 4


def test_v1_database_upgrades_in_place_and_preserves_data(tmp_path):
    path = tmp_path / "aircontrol.db"
    trajectory = _trajectory("Right")
    trajectory_blob = serialize(trajectory)
    gesture_row = (41, "Legacy Wave", "Preserve me", 123.5, 456.75)
    exemplar_row = (73, 41, trajectory_blob, 2, 789.25, "Right")

    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (1);

            CREATE TABLE gestures (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE exemplars (
                id INTEGER PRIMARY KEY,
                gesture_id INTEGER NOT NULL,
                trajectory BLOB NOT NULL,
                frame_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                handedness TEXT,
                FOREIGN KEY (gesture_id) REFERENCES gestures(id)
                    ON DELETE CASCADE
            );

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
            );

            CREATE TABLE calibration_profiles (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                payload TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO gestures (
                id, name, description, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            gesture_row,
        )
        connection.execute(
            """
            INSERT INTO exemplars (
                id, gesture_id, trajectory, frame_count, created_at, handedness
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            exemplar_row,
        )

    with Store(path) as store:
        assert store.schema_version == 4
        assert store.gestures.get(41) == GestureRecord(
            id=41,
            name="Legacy Wave",
            description="Preserve me",
            created_at=123.5,
            updated_at=456.75,
        )
        assert store.exemplars.count(41) == 1
        assert store.exemplars.list(41) == [
            deserialize(trajectory_blob, "Right")
        ]

    with Store(path) as reopened:
        assert reopened.schema_version == 4
        assert reopened.gestures.get(41) is not None
        assert reopened.exemplars.count(41) == 1

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchall() == [(4,)]
        assert connection.execute(
            """
            SELECT id, name, description, created_at, updated_at
            FROM gestures
            """
        ).fetchone() == gesture_row
        assert connection.execute(
            """
            SELECT id, gesture_id, trajectory, frame_count, created_at,
                   handedness
            FROM exemplars
            """
        ).fetchone() == exemplar_row


def test_gesture_stats_get_auto_creates_zero_row(tmp_path):
    from aircontrol.store import GestureStatsRecord, GestureStatsRepo

    path = tmp_path / "aircontrol.db"
    with Store(path) as store:
        gesture = store.gestures.add("Wave")

        stats = store.gesture_stats.get(gesture.id)

        assert isinstance(store.gesture_stats, GestureStatsRepo)
        assert isinstance(stats, GestureStatsRecord)
        assert stats.gesture_id == gesture.id
        assert stats.confirms == 0
        assert stats.rejects == 0
        assert stats.threshold_offset == 0.0
        assert isinstance(stats.updated_at, float)
        assert store.gesture_stats.get(gesture.id) == stats
        assert hasattr(GestureStatsRecord, "__slots__")
        with pytest.raises(FrozenInstanceError):
            stats.confirms = 1

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            """
            SELECT gesture_id, confirms, rejects, threshold_offset, updated_at
            FROM gesture_stats
            """
        ).fetchone() == (
            stats.gesture_id,
            stats.confirms,
            stats.rejects,
            stats.threshold_offset,
            stats.updated_at,
        )


def test_gesture_stats_confirm_and_reject_accumulate(tmp_path):
    with Store(tmp_path / "aircontrol.db") as store:
        gesture = store.gestures.add("Pinch")

        store.gesture_stats.record_confirm(gesture.id)
        store.gesture_stats.record_confirm(gesture.id)
        store.gesture_stats.record_reject(gesture.id)
        after_first_reject = store.gesture_stats.get(gesture.id)
        store.gesture_stats.record_reject(gesture.id)
        after_second_reject = store.gesture_stats.get(gesture.id)

        assert after_first_reject.confirms == 2
        assert after_first_reject.rejects == 1
        assert after_first_reject.threshold_offset == pytest.approx(0.02)
        assert after_second_reject.confirms == 2
        assert after_second_reject.rejects == 2
        assert after_second_reject.threshold_offset == pytest.approx(0.04)


def test_gesture_stats_reset_zeroes_values(tmp_path):
    with Store(tmp_path / "aircontrol.db") as store:
        gesture = store.gestures.add("Swipe")
        store.gesture_stats.record_confirm(gesture.id)
        store.gesture_stats.record_reject(gesture.id, offset_bump=0.05)

        store.gesture_stats.reset(gesture.id)

        stats = store.gesture_stats.get(gesture.id)
        assert stats.confirms == 0
        assert stats.rejects == 0
        assert stats.threshold_offset == 0.0


def test_deleting_gesture_cascades_to_gesture_stats(tmp_path):
    path = tmp_path / "aircontrol.db"
    with Store(path) as store:
        gesture = store.gestures.add("Rotate")
        store.gesture_stats.get(gesture.id)

        store.gestures.delete(gesture.id)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM gesture_stats WHERE gesture_id = ?",
            (gesture.id,),
        ).fetchone() == (0,)


def test_delete_everything_clears_gesture_stats(tmp_path):
    path = tmp_path / "aircontrol.db"
    with Store(path) as store:
        gesture = store.gestures.add("Circle")
        store.gesture_stats.record_confirm(gesture.id)
        store.gesture_stats.record_reject(gesture.id)

        store.delete_everything()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM gesture_stats"
        ).fetchone() == (0,)


def test_gesture_kind_defaults_to_motion_and_round_trips_pose(tmp_path):
    with Store(tmp_path / "aircontrol.db") as store:
        motion = store.gestures.add("Wave")
        pose = store.gestures.add("Peace sign", kind="pose")

        assert motion.kind == "motion"
        assert pose.kind == "pose"
        assert store.gestures.get(motion.id).kind == "motion"
        assert store.gestures.get(pose.id).kind == "pose"
        kinds = {gesture.id: gesture.kind for gesture in store.gestures.list()}
        assert kinds == {motion.id: "motion", pose.id: "pose"}


def test_gesture_add_rejects_unsupported_kind(tmp_path):
    with Store(tmp_path / "aircontrol.db") as store:
        with pytest.raises(ValueError):
            store.gestures.add("Bad", kind="wiggle")


def test_v3_database_upgrades_to_v4_and_defaults_kind_to_motion(tmp_path):
    path = tmp_path / "aircontrol.db"
    now = 1_700_000_000.0
    gesture_row = (1, "Legacy Wave", "Preserve me", now, now)

    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (3);

            CREATE TABLE gestures (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE exemplars (
                id INTEGER PRIMARY KEY,
                gesture_id INTEGER NOT NULL,
                trajectory BLOB NOT NULL,
                frame_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                handedness TEXT,
                FOREIGN KEY (gesture_id) REFERENCES gestures(id)
                    ON DELETE CASCADE
            );

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
            );

            CREATE TABLE calibration_profiles (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                payload TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE gesture_stats (
                gesture_id INTEGER PRIMARY KEY
                    REFERENCES gestures(id) ON DELETE CASCADE,
                confirms INTEGER NOT NULL DEFAULT 0,
                rejects INTEGER NOT NULL DEFAULT 0,
                threshold_offset REAL NOT NULL DEFAULT 0.0,
                updated_at REAL NOT NULL
            );

            CREATE TABLE app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO gestures (id, name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            gesture_row,
        )

    with Store(path) as store:
        assert store.schema_version == 4
        gesture = store.gestures.get(1)
        assert gesture is not None
        assert gesture.kind == "motion"

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(gestures)")}
    assert "kind" in columns

    with Store(path) as reopened:
        assert reopened.schema_version == 4
        assert reopened.gestures.get(1).kind == "motion"
