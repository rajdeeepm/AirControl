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


def test_fresh_database_has_schema_version_one(tmp_path):
    path = tmp_path / "aircontrol.db"

    with Store(path) as store:
        assert store.schema_version == 1
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
        assert reopened.schema_version == 1
        assert reopened.gestures.get(gesture.id) == gesture

    with sqlite3.connect(path) as connection:
        versions = connection.execute("SELECT version FROM schema_version").fetchall()
    assert versions == [(1,)]


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
        assert store.schema_version == 1


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
