from __future__ import annotations

import json

import pytest

from test_matcher_integration import (
    _add_gesture,
    _candidate_trajectory,
    _configured_app,
    _feed_pipeline,
    _make_pipeline,
    _profile,
    _scripted_observations,
    _seed_two_gestures,
    _strict_gate,
    _PRESENCE_FRAMES,
)

from aircontrol import cli
from aircontrol.arena import ArenaSession
from aircontrol.matcher import DtwMatcher
from aircontrol.profile import save_profile
from aircontrol.recording import RecordingConfig, RecordingSession
from aircontrol.store import Store
from aircontrol.trajectory import LandmarkFrame


def test_threshold_offset_converts_fire_to_t1_offset_abstain() -> None:
    with Store(":memory:") as store:
        gesture_ids = _seed_two_gestures(store)
        store.gesture_stats.record_reject(
            gesture_ids["horizontal"],
            offset_bump=0.5,
        )
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        events = _feed_pipeline(pipeline, clock, "horizontal")

        candidates = [event for event in events if event["type"] == "candidate"]
        assert [(event["gate"], event["reason"]) for event in candidates] == [
            ("abstain", "t1_offset")
        ]
        assert not any(event["type"] == "action" for event in events)
        assert pipeline.controller.sink.events == []


def test_zero_offset_still_fires() -> None:
    with Store(":memory:") as store:
        _seed_two_gestures(store)
        pipeline, clock = _make_pipeline(store, gate=_strict_gate())

        events = _feed_pipeline(pipeline, clock, "horizontal")

        candidates = [event for event in events if event["type"] == "candidate"]
        assert [(event["gate"], event["reason"]) for event in candidates] == [
            ("fire", "ok")
        ]
        assert [
            event["kind"] for event in events if event["type"] == "action"
        ] == ["switch_next"]


def _scripted_frames(kind: str, **kwargs) -> list[tuple[float, LandmarkFrame]]:
    return [
        (
            now,
            LandmarkFrame(
                landmarks=observation.landmarks,
                handedness=observation.handedness,
                timestamp=now,
            ),
        )
        for now, observation in _scripted_observations(kind, **kwargs)
    ]


def test_recording_to_arena_round_trip_dispatches_new_gesture() -> None:
    with Store(":memory:") as store:
        profile = _profile()
        config = _configured_app()
        session = RecordingSession(
            "Wave",
            store,
            profile,
            config,
            RecordingConfig(min_takes=4, max_takes=6),
        )

        base_time = 0.0
        for take_index in range(4):
            for now, frame in _scripted_frames(
                "vertical",
                amplitude=1.0 + (take_index - 1.5) * 0.02,
                noise=0.004 + take_index * 0.001,
                phase=take_index * 0.41,
            ):
                event = session.feed(
                    LandmarkFrame(
                        landmarks=frame.landmarks,
                        handedness=frame.handedness,
                        timestamp=base_time + now,
                    ),
                    base_time + now,
                )
                if event is not None:
                    session.confirm_take()
            # a gap between takes so segmentation resets cleanly
            session.feed(None, base_time + 10.0)
            base_time += 20.0

        outcome = session.finish()
        assert outcome.saved, outcome.reason
        gesture_id = outcome.gesture_id
        assert gesture_id is not None
        store.mappings.set(gesture_id, {"kind": "scroll", "amount": 2})

        before = store.exemplars.count(gesture_id)
        matcher = DtwMatcher(store)
        matcher.refresh()
        arena = ArenaSession(store)
        trajectory = _candidate_trajectory("vertical")
        result = matcher.match(trajectory)
        assert result.gesture_id == gesture_id

        from aircontrol.segmentation import CandidateSegment

        segment = CandidateSegment(
            trajectory=trajectory,
            t_onset=trajectory.frames[0].timestamp,
            t_offset=trajectory.frames[-1].timestamp,
        )
        arena.observe(segment, result, fired=True)
        arena.confirm()
        assert store.exemplars.count(gesture_id) == before + 1

        pipeline, clock = _make_pipeline(store, gate=_strict_gate())
        events = _feed_pipeline(pipeline, clock, "vertical")
        assert [
            event["kind"] for event in events if event["type"] == "action"
        ] == ["scroll"]


def test_cli_help_lists_new_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--record-gesture" in out
    assert "--arena" in out
    assert "--stress" in out


def test_cli_mode_flags_are_mutually_exclusive() -> None:
    for argv in (
        ["--record-gesture", "Wave", "--calibrate"],
        ["--record-gesture", "Wave", "--arena"],
        ["--arena", "--practice"],
        ["--stress"],
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == 2


def test_record_gesture_without_profile_exits_2_before_camera(
    tmp_path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "store.db"
    config_payload = {"store": {"db_path": str(db_path)}}
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_payload), encoding="utf-8")

    def _camera_bomb(*args, **kwargs):
        raise AssertionError("camera must not be opened without a profile")

    import aircontrol.app as app_module

    monkeypatch.setattr(app_module, "AsyncVisionWorker", _camera_bomb)
    monkeypatch.setattr(
        app_module,
        "ensure_hand_model",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("model must not be resolved without a profile")
        ),
    )

    exit_code = cli.main(
        ["--config", str(config_file), "--record-gesture", "Wave"]
    )
    assert exit_code == 2
    output = capsys.readouterr()
    assert "calibrat" in (output.out + output.err).lower()


def test_arena_without_profile_exits_2_before_camera(
    tmp_path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "store.db"
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"store": {"db_path": str(db_path)}}), encoding="utf-8"
    )

    def _camera_bomb(*args, **kwargs):
        raise AssertionError("camera must not be opened without a profile")

    import aircontrol.app as app_module

    monkeypatch.setattr(app_module, "AsyncVisionWorker", _camera_bomb)

    exit_code = cli.main(["--config", str(config_file), "--arena"])
    assert exit_code == 2
    output = capsys.readouterr()
    assert "calibrat" in (output.out + output.err).lower()
