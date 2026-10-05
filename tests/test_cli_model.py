import pytest

from systemone_gate import cli


@pytest.fixture
def seen_model(monkeypatch):
    seen = {}

    def fake_handle_diff(client, model, **kwargs):
        seen["model"] = model
        return 0

    monkeypatch.setattr(cli, "handle_diff", fake_handle_diff)
    monkeypatch.delenv("SYSTEMONE_DIFF_MODEL", raising=False)
    return seen


def _exit_code(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    return e.value.code


def test_default_model_is_named_constant(seen_model):
    assert _exit_code(["diff"]) == 0
    assert seen_model["model"] == cli.DEFAULT_DIFF_MODEL == "nimble"


def test_nimble_flag_selects_nimble(seen_model):
    _exit_code(["diff", "--nimble"])
    assert seen_model["model"] == "nimble"


def test_model_flag_selects_any_model(seen_model):
    _exit_code(["diff", "--model", "qwen:7b"])
    assert seen_model["model"] == "qwen:7b"


def test_env_overrides_default(seen_model, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_DIFF_MODEL", "nimble")
    _exit_code(["diff"])
    assert seen_model["model"] == "nimble"


def test_empty_env_falls_back_to_default(seen_model, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_DIFF_MODEL", "")
    _exit_code(["diff"])
    assert seen_model["model"] == cli.DEFAULT_DIFF_MODEL


def test_flags_beat_env(seen_model, monkeypatch):
    monkeypatch.setenv("SYSTEMONE_DIFF_MODEL", "nimble")
    _exit_code(["diff", "--model", "other"])
    assert seen_model["model"] == "other"
    _exit_code(["diff", "--nimble"])
    assert seen_model["model"] == "nimble"


def test_model_and_nimble_are_mutually_exclusive(seen_model):
    assert _exit_code(["diff", "--model", "x", "--nimble"]) == 2
    assert "model" not in seen_model


def test_empty_model_flag_is_usage_error(seen_model):
    assert _exit_code(["diff", "--model", ""]) == 2
    assert "model" not in seen_model
