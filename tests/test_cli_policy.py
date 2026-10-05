import pytest

from systemone_gate import cli

DIFF_TEXT = "diff --git a/x b/x\n+line\n"


class StubClient:
    def __init__(self, response):
        self.response = response

    def review_diff(self, diff, model=None):
        return self.response

    def guard_command(self, command, model=None):
        return self.response


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "SYSTEMONE_DIFF_RISK_THRESHOLD",
        "SYSTEMONE_DIFF_BREAKING_THRESHOLD",
        "SYSTEMONE_GUARD_DANGER_THRESHOLD",
        "SYSTEMONE_DIFF_ON_ERROR",
        "SYSTEMONE_GUARD_ON_ERROR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli.subprocess, "check_output", lambda *a, **k: DIFF_TEXT)


def guard_res(choice, danger, **extra):
    res = {"answers": {"is_destructive": {"choice": choice}, "danger_score": {"score": danger}}}
    res.update(extra)
    return res


def diff_res(risk, breaking):
    return {
        "answers": {
            "risk_level": {"score": risk},
            "breaking_change": {
                "choice": "breaking_change",
                "probabilities": {"breaking_change": breaking, "safe": 1 - breaking},
            },
        }
    }


# ------------------------------------------------------------------ guard

def test_guard_allows_safe(capsys):
    assert cli.handle_guard(StubClient(guard_res("safe", 0.2)), "ls", "m") == 0
    out = capsys.readouterr()
    assert "Destrutivo: safe" in out.out and "0.20" in out.out
    assert out.err == ""


def test_guard_blocks_destructive(capsys):
    assert cli.handle_guard(StubClient(guard_res("destructive_or_risky", 1.9)), "rm -rf /", "m") == 1
    assert "COMANDO BLOQUEADO" in capsys.readouterr().err


def test_guard_blocks_rules_source(capsys):
    assert cli.handle_guard(StubClient(guard_res("safe", 0.0, source="rules")), "x", "m") == 1
    assert "COMANDO BLOQUEADO" in capsys.readouterr().err


def test_guard_error_fails_open_with_warning(capsys):
    assert cli.handle_guard(StubClient({"error": "ollama down"}), "ls", "m") == 0
    assert "ollama down" in capsys.readouterr().err


def test_guard_error_blocks_when_configured(monkeypatch, capsys):
    monkeypatch.setenv("SYSTEMONE_GUARD_ON_ERROR", "block")
    assert cli.handle_guard(StubClient({"error": "ollama down"}), "ls", "m") == 1
    assert "ollama down" in capsys.readouterr().err


def test_guard_malformed_is_not_silently_safe(capsys):
    assert cli.handle_guard(StubClient({"answers": {}}), "rm -rf /", "m") == 0
    err = capsys.readouterr()
    assert "resposta inválida" in err.err
    assert "Destrutivo" not in err.out


def test_guard_malformed_blocks_when_configured(monkeypatch, capsys):
    monkeypatch.setenv("SYSTEMONE_GUARD_ON_ERROR", "block")
    assert cli.handle_guard(StubClient({"answers": {}}), "x", "m") == 1
    assert "resposta inválida" in capsys.readouterr().err


def test_guard_threshold_override_changes_verdict(monkeypatch):
    client = StubClient(guard_res("destructive_or_risky", 1.0))
    assert cli.handle_guard(client, "x", "m") == 0
    monkeypatch.setenv("SYSTEMONE_GUARD_DANGER_THRESHOLD", "0.5")
    assert cli.handle_guard(client, "x", "m") == 1


def test_guard_invalid_env_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("SYSTEMONE_GUARD_ON_ERROR", "maybe")
    assert cli.handle_guard(StubClient(guard_res("safe", 0)), "x", "m") == 2
    assert "SYSTEMONE_GUARD_ON_ERROR" in capsys.readouterr().err


# ------------------------------------------------------------------ diff

def test_diff_allows(capsys):
    assert cli.handle_diff(StubClient(diff_res(1.0, 0.9)), "m") == 0
    out = capsys.readouterr()
    assert "APROVADO" in out.out and "1.00" in out.out


def test_diff_blocks(capsys):
    assert cli.handle_diff(StubClient(diff_res(1.95, 0.9)), "m") == 1
    assert "BLOQUEIO ATIVADO" in capsys.readouterr().err


def test_diff_error_fails_open_with_warning(capsys):
    assert cli.handle_diff(StubClient({"error": "ollama down"}), "m") == 0
    assert "ollama down" in capsys.readouterr().err


def test_diff_error_blocks_when_configured(monkeypatch):
    monkeypatch.setenv("SYSTEMONE_DIFF_ON_ERROR", "block")
    assert cli.handle_diff(StubClient({"error": "down"}), "m") == 1


def test_diff_malformed_warns_not_approved(capsys):
    assert cli.handle_diff(StubClient({"answers": {"risk_level": {}}}), "m") == 0
    out = capsys.readouterr()
    assert "resposta inválida" in out.err
    assert "APROVADO" not in out.out


def test_diff_threshold_override_changes_verdict(monkeypatch):
    client = StubClient(diff_res(1.2, 0.8))
    assert cli.handle_diff(client, "m") == 0
    monkeypatch.setenv("SYSTEMONE_DIFF_RISK_THRESHOLD", "1.0")
    monkeypatch.setenv("SYSTEMONE_DIFF_BREAKING_THRESHOLD", "0.5")
    assert cli.handle_diff(client, "m") == 1


def test_diff_invalid_env_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("SYSTEMONE_DIFF_RISK_THRESHOLD", "abc")
    assert cli.handle_diff(StubClient(diff_res(1, 1)), "m") == 2
    assert "SYSTEMONE_DIFF_RISK_THRESHOLD" in capsys.readouterr().err


def test_diff_empty_staged_still_allows(monkeypatch, capsys):
    monkeypatch.setattr(cli.subprocess, "check_output", lambda *a, **k: "  \n")
    assert cli.handle_diff(StubClient({}), "m") == 0
