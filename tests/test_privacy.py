import json

import pytest

from aircontrol.privacy import MetricsConsentDeclined, ensure_metrics_consent, has_metrics_consent


def test_explicit_agreement_is_saved_and_reused(tmp_path):
    prompts = []
    output = []

    ensure_metrics_consent(
        tmp_path,
        input_fn=lambda prompt: prompts.append(prompt) or "AGREE",
        output_fn=output.append,
    )

    assert has_metrics_consent(tmp_path)
    payload = json.loads((tmp_path / ".aircontrol-consent.json").read_text())
    assert payload == {"accepted": True, "notice_version": 1}
    assert prompts and output

    ensure_metrics_consent(
        tmp_path,
        input_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("must not reprompt")),
    )


def test_anything_other_than_agree_cancels_without_saving(tmp_path):
    with pytest.raises(MetricsConsentDeclined):
        ensure_metrics_consent(tmp_path, input_fn=lambda _prompt: "no", output_fn=lambda _text: None)

    assert not has_metrics_consent(tmp_path)

