from systemone_gate.client import SystemOneClient

CLOSED_PORT = "http://127.0.0.1:9/"


def test_matching_command_short_circuits_without_network():
    client = SystemOneClient(endpoint=CLOSED_PORT)
    result = client.guard_command("rm -rf /")
    assert "error" not in result
    assert result["source"] == "rules"
    assert result["model"] == "rules"
    assert result["rule"]["id"] == "rm-recursive-root"
    assert result["rule"]["reason"]
    answers = result["answers"]
    assert answers["is_destructive"]["choice"] == "destructive_or_risky"
    assert answers["danger_score"]["score"] == 2.0
    assert result["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_rules_result_is_blocked_by_cli_thresholds():
    result = SystemOneClient(endpoint=CLOSED_PORT).guard_command("DROP TABLE users;")
    answers = result["answers"]
    assert answers["is_destructive"]["choice"] == "destructive_or_risky"
    assert answers["danger_score"]["score"] > 1.5


def test_non_matching_command_goes_to_model_and_error_is_untouched():
    client = SystemOneClient(endpoint=CLOSED_PORT)
    result = client.guard_command("ls -la")
    assert "error" in result
    assert "source" not in result


def test_model_result_is_tagged_without_mutating_input(monkeypatch):
    client = SystemOneClient(endpoint=CLOSED_PORT)
    model_result = {"model": "tev1:0.8b", "answers": {}, "usage": {}}
    monkeypatch.setattr(client, "evaluate", lambda *a, **k: model_result)
    result = client.guard_command("ls -la")
    assert result["source"] == "model"
    assert "source" not in model_result
    assert result is not model_result
