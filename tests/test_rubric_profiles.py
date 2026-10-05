import copy
import json
import subprocess
import sys

import pytest

from systemone_gate import rubrics
from systemone_gate.cli import main
from systemone_gate.client import SystemOneClient
from systemone_gate.diff_review import review_staged

ORIGINAL_DIFF = {
    "risk_level": {
        "type": "score",
        "instructions": "Avalie o nível de risco técnico deste diff de código:",
        "criteria": [
            "Baixo: seguro, documentação, comentários ou refatoração cosmética",
            "Médio: nova função isolada, correção simples de bug com baixo acoplamento",
            "Alto: modificação em concorrência, locks, alocação de memória ou structs de socket",
        ],
    },
    "breaking_change": {
        "type": "choice",
        "instructions": "Esta alteração quebra contratos públicos, APIs ou protocolos?",
        "criteria": {"safe": None, "potential_break": None, "breaking_change": None},
    },
}
ORIGINAL_TRIAGE = {
    "root_cause": {
        "type": "choice",
        "instructions": "Qual é a causa-raiz principal desta falha ou erro de compilação/teste?",
        "criteria": {
            "compilation_syntax": None,
            "linker_undefined_reference": None,
            "memory_segfault_or_leak": None,
            "network_socket_timeout": None,
            "protocol_parsing_error": None,
            "test_assertion_failure": None,
            "environment_or_missing_dep": None,
        },
    },
    "severity": {
        "type": "score",
        "instructions": "Qual o nível de gravidade deste erro?",
        "criteria": [
            "Aviso não bloqueante ou estético",
            "Falha parcial ou teste isolado",
            "Erro bloqueante crítico de compilação ou execução",
        ],
    },
}
C_JARGON = ("socket", "mqtt", "protocol_parsing", "segfault", "linker", "locks")


def test_profiles_and_default_constants():
    assert rubrics.PROFILES == ("default", "generic", "web-backend")
    assert rubrics.DEFAULT_PROFILE == "default"


def test_default_equals_original_literals():
    assert rubrics.get_diff_rubric("default") == ORIGINAL_DIFF
    assert rubrics.get_triage_rubric("default") == ORIGINAL_TRIAGE
    assert rubrics.get_diff_rubric() == ORIGINAL_DIFF
    assert rubrics.get_triage_rubric() == ORIGINAL_TRIAGE
    assert rubrics.RUBRIC_DIFF_RISK == ORIGINAL_DIFF
    assert rubrics.RUBRIC_ERROR_TRIAGE == ORIGINAL_TRIAGE
    assert json.dumps(rubrics.get_diff_rubric("default")) == json.dumps(ORIGINAL_DIFF)
    assert json.dumps(rubrics.get_triage_rubric("default")) == json.dumps(ORIGINAL_TRIAGE)


@pytest.mark.parametrize("profile", rubrics.PROFILES)
def test_diff_structure(profile):
    r = rubrics.get_diff_rubric(profile)
    assert list(r) == ["risk_level", "breaking_change"]
    risk = r["risk_level"]
    assert risk["type"] == "score" and risk["instructions"].strip()
    assert len(risk["criteria"]) == 3 and all(isinstance(c, str) and c.strip() for c in risk["criteria"])
    bc = r["breaking_change"]
    assert bc["type"] == "choice" and bc["instructions"].strip()
    assert list(bc["criteria"]) == ["safe", "potential_break", "breaking_change"]
    assert all(v is None for v in bc["criteria"].values())
    assert json.loads(json.dumps(r)) == r


@pytest.mark.parametrize("profile", rubrics.PROFILES)
def test_triage_structure(profile):
    r = rubrics.get_triage_rubric(profile)
    assert list(r) == ["root_cause", "severity"]
    rc = r["root_cause"]
    assert rc["type"] == "choice" and rc["instructions"].strip()
    assert len(rc["criteria"]) >= 2 and all(v is None for v in rc["criteria"].values())
    assert r["severity"] == ORIGINAL_TRIAGE["severity"]
    assert json.loads(json.dumps(r)) == r


@pytest.mark.parametrize("getter", [rubrics.get_diff_rubric, rubrics.get_triage_rubric])
@pytest.mark.parametrize("profile", rubrics.PROFILES)
def test_no_sharing_or_mutation_between_calls(getter, profile):
    first = getter(profile)
    snapshot = copy.deepcopy(first)
    for question in first.values():
        question["instructions"] = "MUTATED"
        crit = question["criteria"]
        crit.append("x") if isinstance(crit, list) else crit.update({"x": None})
    first["extra"] = {}
    assert getter(profile) == snapshot


def test_module_constants_not_aliased_by_getters():
    rubrics.get_diff_rubric("default")["risk_level"]["criteria"].append("x")
    rubrics.get_triage_rubric("default")["severity"]["criteria"].append("x")
    assert rubrics.RUBRIC_DIFF_RISK == ORIGINAL_DIFF
    assert rubrics.RUBRIC_ERROR_TRIAGE == ORIGINAL_TRIAGE


def test_generic_has_no_c_network_jargon():
    text = json.dumps([rubrics.get_diff_rubric("generic"), rubrics.get_triage_rubric("generic")]).lower()
    assert not any(word in text for word in C_JARGON)
    assert rubrics.get_diff_rubric("generic") != ORIGINAL_DIFF


def test_web_backend_diff_covers_required_topics():
    r = rubrics.get_diff_rubric("web-backend")
    high = r["risk_level"]["criteria"][2].lower()
    for topic in ("migra", "drop", "not null", "autentica", "autoriza", "tenant", "rest", "graphql",
                  "transa", "concorr", "segredo"):
        assert topic in high, topic
    assert high.startswith("alto")
    instr = r["breaking_change"]["instructions"].lower()
    for topic in ("api", "schema", "evento"):
        assert topic in instr, topic


def test_web_backend_triage_keys():
    keys = set(rubrics.get_triage_rubric("web-backend")["root_cause"]["criteria"])
    assert keys == {
        "compilation_or_type_error", "dependency_or_environment", "database_or_migration_error",
        "authentication_or_permission_error", "validation_or_contract_error", "network_or_timeout",
        "test_assertion_failure", "unhandled_runtime_exception",
    }


@pytest.mark.parametrize("getter", [rubrics.get_diff_rubric, rubrics.get_triage_rubric])
def test_unknown_profile_lists_valid_names(getter):
    with pytest.raises(ValueError) as exc:
        getter("nope")
    msg = str(exc.value)
    assert "nope" in msg
    for name in rubrics.PROFILES:
        assert name in msg


# ---------- client ----------

@pytest.mark.parametrize("method,getter,arg", [
    ("review_diff", rubrics.get_diff_rubric, "diff --git a b"),
    ("triage_error", rubrics.get_triage_rubric, "boom"),
])
def test_client_explicit_profile_sent(fake, client, method, getter, arg):
    getattr(client(fake.url), method)(arg, profile="web-backend")
    assert fake.requests[0]["questions"] == getter("web-backend")


@pytest.mark.parametrize("method,getter,arg", [
    ("review_diff", rubrics.get_diff_rubric, "diff --git a b"),
    ("triage_error", rubrics.get_triage_rubric, "boom"),
])
def test_client_env_profile_and_explicit_precedence(fake, client, monkeypatch, method, getter, arg):
    monkeypatch.setenv("SYSTEMONE_PROFILE", "generic")
    c = client(fake.url)
    getattr(c, method)(arg)
    getattr(c, method)(arg, profile="web-backend")
    assert fake.requests[0]["questions"] == getter("generic")
    assert fake.requests[1]["questions"] == getter("web-backend")


@pytest.mark.parametrize("method,getter,arg", [
    ("review_diff", rubrics.get_diff_rubric, "d"),
    ("triage_error", rubrics.get_triage_rubric, "e"),
])
def test_client_default_when_nothing_set(fake, client, monkeypatch, method, getter, arg):
    monkeypatch.delenv("SYSTEMONE_PROFILE", raising=False)
    getattr(client(fake.url), method)(arg)
    assert fake.requests[0]["questions"] == getter("default")


@pytest.mark.parametrize("method", ["review_diff", "triage_error"])
def test_client_invalid_arg_and_env_raise(fake, client, monkeypatch, method):
    c = client(fake.url)
    with pytest.raises(ValueError):
        getattr(c, method)("x", profile="bogus")
    monkeypatch.setenv("SYSTEMONE_PROFILE", "bogus")
    with pytest.raises(ValueError) as exc:
        getattr(c, method)("x")
    assert "SYSTEMONE_PROFILE" in str(exc.value)
    assert fake.requests == []


# ---------- diff_review ----------

class _RecordingClient:
    def __init__(self):
        self.calls = []

    def review_diff(self, text, model=None, profile=None):
        self.calls.append(profile)
        return {"answers": {"risk_level": {"score": 0.1},
                            "breaking_change": {"choice": "safe", "probabilities": {}}}}


def _two_file_diff():
    return ("diff --git a/a.ts b/a.ts\n+x\n"
            "diff --git a/b.ts b/b.ts\n+y\n")


def test_review_staged_forwards_profile_to_every_file():
    c = _RecordingClient()
    review_staged(c, _two_file_diff(), "m", profile="web-backend")
    assert c.calls == ["web-backend", "web-backend"]


def test_review_staged_default_does_not_pass_profile():
    class Legacy:
        def review_diff(self, text, model=None):
            return {"answers": {"risk_level": {"score": 0.0},
                                "breaking_change": {"choice": "safe", "probabilities": {}}}}
    assert "answers" in review_staged(Legacy(), _two_file_diff(), "m")


# ---------- CLI ----------

def _run(args, url, sub_env, repo_root, extra_env=None, cwd=None):
    env = sub_env(url)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-m", "systemone_gate.cli"] + args, cwd=cwd or repo_root,
                          env=env, capture_output=True, text=True, timeout=30)


def test_cli_triage_profile_flag(fake, sub_env, repo_root):
    out = _run(["triage", "boom", "--profile", "web-backend"], fake.url, sub_env, repo_root)
    assert out.returncode == 0, out.stderr
    assert fake.requests[0]["questions"] == rubrics.get_triage_rubric("web-backend")


def test_cli_triage_env_profile(fake, sub_env, repo_root):
    out = _run(["triage", "boom"], fake.url, sub_env, repo_root, {"SYSTEMONE_PROFILE": "generic"})
    assert out.returncode == 0, out.stderr
    assert fake.requests[0]["questions"] == rubrics.get_triage_rubric("generic")


def test_cli_invalid_profile_choice_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        main(["triage", "x", "--profile", "bogus"])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["triage", "boom"]])
def test_cli_bad_env_profile_exits_2(fake, sub_env, repo_root, args):
    out = _run(args, fake.url, sub_env, repo_root, {"SYSTEMONE_PROFILE": "bogus"})
    assert out.returncode == 2
    assert "Configuração inválida" in out.stderr
    assert fake.requests == []


def _git_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "a.ts").write_text("export const a = 1;\n")
    git("add", "a.ts")


def test_cli_diff_profile_flag_and_bad_env(fake, sub_env, repo_root, tmp_path):
    _git_repo(tmp_path)
    out = _run(["diff", "--profile", "web-backend"], fake.url, sub_env, repo_root, cwd=tmp_path,
               extra_env={"PYTHONPATH": repo_root})
    assert out.returncode == 0, out.stderr
    assert fake.requests[0]["questions"] == rubrics.get_diff_rubric("web-backend")
    bad = _run(["diff"], fake.url, sub_env, repo_root, cwd=tmp_path,
               extra_env={"PYTHONPATH": repo_root, "SYSTEMONE_PROFILE": "bogus"})
    assert bad.returncode == 2
    assert "Configuração inválida" in bad.stderr
    assert len(fake.requests) == 1


# --- combinação de --model/--nimble com --profile (resolução do merge dos PRs #17 e #21) ---

@pytest.mark.parametrize(
    "argv, expected_model",
    [
        (["diff", "--model", "foo", "--profile", "web-backend"], "foo"),
        (["diff", "--nimble", "--profile", "generic"], "nimble"),
        (["diff", "--profile", "web-backend"], "tev1:0.8b"),
    ],
)
def test_diff_forwards_resolved_model_and_profile(monkeypatch, argv, expected_model):
    from systemone_gate import cli

    for var in ("SYSTEMONE_DIFF_MODEL", "SYSTEMONE_PROFILE", "SYSTEMONE_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    seen = {}

    def fake_handle_diff(client, model, *args, **kwargs):
        seen["model"] = model
        seen["profile"] = kwargs.get("profile")
        return 0

    monkeypatch.setattr(cli, "handle_diff", fake_handle_diff)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)
    assert exit_info.value.code == 0
    assert seen["model"] == expected_model
    assert seen["profile"] == argv[argv.index("--profile") + 1]
