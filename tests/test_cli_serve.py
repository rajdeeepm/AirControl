from __future__ import annotations

import json

import pytest

from aircontrol import cli


def test_serve_appears_in_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])

    assert excinfo.value.code == 0
    assert "--serve" in capsys.readouterr().out


@pytest.mark.parametrize(
    "mode_args",
    [
        ["--calibrate"],
        ["--record-gesture", "Wave"],
        ["--arena"],
    ],
)
def test_serve_cannot_be_combined_with_special_modes(
    mode_args: list[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--serve", *mode_args])

    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    ("run_args", "expected_practice"),
    [
        ([], False),
        (["--practice"], True),
    ],
)
def test_serve_enables_ipc_before_run_without_opening_camera(
    tmp_path,
    monkeypatch,
    run_args: list[str],
    expected_practice: bool,
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"ipc": {"enabled": False}}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def _run(**kwargs) -> int:
        captured.update(kwargs)
        return 0

    import aircontrol.app as app_module

    monkeypatch.setattr(app_module, "run", _run)
    monkeypatch.setattr(
        app_module,
        "AsyncVisionWorker",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("camera must not be opened before run")
        ),
    )

    exit_code = cli.main(
        ["--config", str(config_file), "--serve", *run_args]
    )

    assert exit_code == 0
    assert captured["practice"] is expected_practice
    assert captured["config"].ipc.enabled is True
