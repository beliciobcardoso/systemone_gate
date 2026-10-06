import copy

import pytest

from systemone_gate import cli
from systemone_gate.diff_review import (
    FileDiff,
    ignore_reason,
    review_staged,
    split_diff_by_file,
)


def make_file_diff(path, body_lines, old_path=None):
    old = old_path or path
    head = [
        f"diff --git a/{old} b/{path}",
        "index 111..222 100644",
        f"--- a/{old}",
        f"+++ b/{path}",
        f"@@ -1,{len(body_lines)} +1,{len(body_lines)} @@",
    ]
    return "\n".join(head + [f"+{line}" for line in body_lines]) + "\n"


def answer(score, break_prob, choice="safe"):
    return {
        "answers": {
            "risk_level": {
                "score": score,
                "legend": {"0": "baixo", "2": "alto"},
                "probabilities": {"0": 1 - score / 2, "2": score / 2},
            },
            "breaking_change": {
                "choice": choice,
                "probabilities": {"breaking_change": break_prob, "safe": 1 - break_prob},
            },
        }
    }


class StubClient:
    def __init__(self, responses=None, default=None):
        self.calls = []
        self._responses = list(responses or [])
        self._default = default if default is not None else answer(0.1, 0.1)

    def review_diff(self, text, model=None):
        self.calls.append({"text": text, "model": model})
        if self._responses:
            return self._responses.pop(0)
        return self._default


# ---------- split ----------

def test_split_multiple_files_preserves_text():
    diff = make_file_diff("a.py", ["x"]) + make_file_diff("src/b.py", ["y", "z"])
    files = split_diff_by_file(diff)
    assert [f.path for f in files] == ["a.py", "src/b.py"]
    assert files[0].text.startswith("diff --git a/a.py b/a.py")
    assert "+y" in files[1].text and "+x" not in files[1].text


def test_split_ignores_preamble_and_empty():
    assert split_diff_by_file("") == ()
    assert split_diff_by_file("garbage\n") == ()
    files = split_diff_by_file("preamble\n" + make_file_diff("a.py", ["x"]))
    assert len(files) == 1 and "preamble" not in files[0].text


def test_split_rename_uses_new_path():
    diff = (
        "diff --git a/old name.py b/new.py\n"
        "similarity index 90%\nrename from old name.py\nrename to new.py\n"
    )
    assert split_diff_by_file(diff)[0].path == "new.py"


def test_split_unquoted_path_with_spaces():
    files = split_diff_by_file(make_file_diff("my dir/my file.py", ["x"]))
    assert files[0].path == "my dir/my file.py"


def test_split_quoted_path_with_escapes():
    diff = 'diff --git "a/my file.py" "b/my file.py"\n--- a\n'
    assert split_diff_by_file(diff)[0].path == "my file.py"
    diff = 'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
    assert split_diff_by_file(diff)[0].path == "café.py"
    diff = 'diff --git "a/q\\"t\\\\n\\t.py" "b/q\\"t\\\\n\\t.py"\n'
    assert split_diff_by_file(diff)[0].path == 'q"t\\n\t.py'


def test_split_quoted_rename_mixed():
    diff = 'diff --git a/old.py "b/new file.py"\n'
    assert split_diff_by_file(diff)[0].path == "new file.py"


def test_split_new_and_deleted_files():
    new = (
        "diff --git a/n.py b/n.py\nnew file mode 100644\nindex 0..1\n"
        "--- /dev/null\n+++ b/n.py\n@@ -0,0 +1 @@\n+x\n"
    )
    deleted = (
        "diff --git a/d.py b/d.py\ndeleted file mode 100644\nindex 1..0\n"
        "--- a/d.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    )
    assert [f.path for f in split_diff_by_file(new + deleted)] == ["n.py", "d.py"]


def test_split_header_fallback_unparsable():
    # asymmetric unquoted header without " b/" -> uses raw remainder
    files = split_diff_by_file("diff --git weird\n")
    assert files[0].path == "weird"


# ---------- ignore ----------

@pytest.mark.parametrize("path", [
    "package-lock.json", "web/yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "uv.lock", "Gemfile.lock", "go.sum", "composer.lock",
    "Pipfile.lock", "bun.lockb",
])
def test_ignore_lockfiles(path):
    assert ignore_reason(FileDiff(path, "x")) == "lockfile"


@pytest.mark.parametrize("path", ["app.min.js", "a/b.min.css", "bundle.js.map"])
def test_ignore_minified(path):
    assert ignore_reason(FileDiff(path, "x")) == "minified/generated"


def test_ignore_binary():
    text = "diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ\n"
    assert ignore_reason(FileDiff("i.png", text)) == "binary"
    text = "diff --git a/i.png b/i.png\nGIT binary patch\nliteral 10\n"
    assert ignore_reason(FileDiff("i.png", text)) == "binary"


def test_not_ignored():
    assert ignore_reason(FileDiff("src/app.py", "+x")) is None
    assert ignore_reason(FileDiff("lockfile.py", "+x")) is None


# ---------- review_staged ----------

def test_lockfile_first_bug_scenario():
    diff = (
        make_file_diff("package-lock.json", [f"l{i}" for i in range(600)])
        + make_file_diff("src/code.py", [f"c{i}" for i in range(20)])
    )
    client = StubClient()
    res = review_staged(client, diff, "m")
    assert len(client.calls) == 1
    assert "src/code.py" in client.calls[0]["text"]
    assert "package-lock" not in client.calls[0]["text"]
    assert client.calls[0]["model"] == "m"
    assert res["coverage"]["reviewed"] == ["src/code.py"]
    assert res["coverage"]["skipped"] == [{"path": "package-lock.json", "reason": "lockfile"}]
    assert res["coverage"]["truncated"] == []


def test_worst_case_aggregation():
    diff = (
        make_file_diff("a.py", ["1"]) + make_file_diff("b.py", ["2"])
        + make_file_diff("c.py", ["3"])
    )
    client = StubClient([
        answer(0.2, 0.9, "breaking_change"),
        answer(1.7, 0.3),
        answer(0.9, 0.5),
    ])
    res = review_staged(client, diff, "m")
    rl = res["answers"]["risk_level"]
    bc = res["answers"]["breaking_change"]
    assert rl["score"] == 1.7
    assert "legend" in rl and "probabilities" in rl
    # breaking answer from file with highest breaking prob (a.py), not riskiest
    assert bc["choice"] == "breaking_change"
    assert bc["probabilities"]["breaking_change"] == 0.9
    assert res["coverage"]["reviewed"] == ["a.py", "b.py", "c.py"]


def test_missing_breaking_probability_defaults_to_zero():
    r1 = {"answers": {"risk_level": {"score": 0.5},
                      "breaking_change": {"choice": "safe"}}}
    r2 = answer(0.1, 0.2)
    res = review_staged(StubClient([r1, r2]),
                        make_file_diff("a.py", ["1"]) + make_file_diff("b.py", ["2"]), "m")
    assert res["answers"]["breaking_change"]["probabilities"]["breaking_change"] == 0.2


def test_missing_risk_and_breaking_sections_tolerated():
    res = review_staged(StubClient([{"answers": {}}]), make_file_diff("a.py", ["1"]), "m")
    assert res["answers"]["risk_level"]["score"] == 0.0
    assert res["answers"]["breaking_change"]["choice"] == "safe"


def test_per_file_truncation():
    diff = make_file_diff("big.py", [f"l{i}" for i in range(400)]) + make_file_diff("s.py", ["x"])
    client = StubClient()
    res = review_staged(client, diff, "m", max_lines_per_file=50)
    sent = client.calls[0]["text"]
    lines = sent.splitlines()
    assert len(lines) == 51
    assert lines[-1].startswith("... [truncated ") and lines[-1].endswith(" lines]")
    assert "\n... [truncated" not in client.calls[1]["text"]
    assert res["coverage"]["truncated"] == ["big.py"]


def test_truncation_marker_count():
    diff = make_file_diff("big.py", [f"l{i}" for i in range(10)])  # 5 header + 10
    client = StubClient()
    review_staged(client, diff, "m", max_lines_per_file=5)
    assert client.calls[0]["text"].splitlines()[-1] == "... [truncated 10 lines]"


def test_max_files_cap():
    diff = "".join(make_file_diff(f"f{i}.py", ["x"]) for i in range(5))
    client = StubClient()
    res = review_staged(client, diff, "m", max_files=2)
    assert len(client.calls) == 2
    assert res["coverage"]["reviewed"] == ["f0.py", "f1.py"]
    assert res["coverage"]["skipped"] == [
        {"path": f"f{i}.py", "reason": "file limit"} for i in (2, 3, 4)
    ]


def test_ignored_files_do_not_consume_max_files():
    diff = (
        make_file_diff("yarn.lock", ["x"]) + make_file_diff("a.py", ["x"])
        + make_file_diff("b.py", ["x"])
    )
    client = StubClient()
    res = review_staged(client, diff, "m", max_files=1)
    assert res["coverage"]["reviewed"] == ["a.py"]
    reasons = {s["path"]: s["reason"] for s in res["coverage"]["skipped"]}
    assert reasons == {"yarn.lock": "lockfile", "b.py": "file limit"}


def test_error_propagates_unchanged_from_any_file():
    err = {"error": "ollama offline"}
    client = StubClient([answer(0.1, 0.1), err])
    res = review_staged(
        client, make_file_diff("a.py", ["1"]) + make_file_diff("b.py", ["2"]), "m"
    )
    assert res == err


def test_malformed_response_becomes_error():
    for bad in ({}, {"answers": None}, {"answers": "x"}, None):
        res = review_staged(StubClient([bad]), make_file_diff("a.py", ["1"]), "m")
        assert res == {"error": "invalid model response for a.py"}


@pytest.mark.parametrize("answers", [
    {"risk_level": {"score": "high"}, "breaking_change": {"probabilities": {}}},
    {"risk_level": {"score": None}},
    {"risk_level": {"score": float("nan")}},
    {"risk_level": {"score": True}},
    {"risk_level": "high"},
    {"breaking_change": {"probabilities": {"breaking_change": "0.9"}}},
    {"breaking_change": {"probabilities": [0.9]}},
    {"breaking_change": "breaking_change"},
])
def test_non_numeric_fields_become_error_not_typeerror(answers):
    client = StubClient([{"answers": answers}, answer(0.1, 0.1)])
    res = review_staged(client, make_file_diff("a.py", ["1"]) + make_file_diff("b.py", ["2"]), "m")
    assert res["error"].startswith("invalid model response for a.py: field '")


def test_cli_non_numeric_score_warns_and_exits_zero(monkeypatch, capsys):
    bad = {"answers": {"risk_level": {"score": "high"}}}
    code = run_cli(monkeypatch, make_file_diff("a.py", ["x"]) + make_file_diff("b.py", ["y"]),
                   StubClient([bad, answer(0.1, 0.1)]))
    assert code == 0
    assert "malformed" in capsys.readouterr().err


def test_only_ignored_files_approves():
    diff = make_file_diff("yarn.lock", ["x"]) + (
        "diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ\n"
    )
    client = StubClient()
    res = review_staged(client, diff, "m")
    assert client.calls == []
    assert res["answers"]["risk_level"]["score"] == 0.0
    assert res["answers"]["breaking_change"] == {"choice": "safe", "probabilities": {}}
    assert res["coverage"]["reviewed"] == []
    assert [s["reason"] for s in res["coverage"]["skipped"]] == ["lockfile", "binary"]


def test_headerless_diff_is_reviewed_whole():
    client = StubClient()
    res = review_staged(client, "--- a\n+++ b\n+x\n", "m")
    assert len(client.calls) == 1
    assert res["coverage"]["reviewed"] == ["(diff)"]


def test_blank_diff_is_empty_result():
    res = review_staged(StubClient(), "  \n", "m")
    assert res["coverage"] == {"reviewed": [], "skipped": [], "truncated": []}


def test_no_input_mutation():
    diff = make_file_diff("a.py", ["1"]) + make_file_diff("b.py", ["2"])
    original = copy.copy(diff)
    r1 = answer(0.3, 0.2)
    snapshot = copy.deepcopy(r1)
    review_staged(StubClient([r1, answer(0.1, 0.1)]), diff, "m")
    assert diff == original and r1 == snapshot


def test_result_not_aliasing_client_response():
    r1 = answer(0.3, 0.2)
    res = review_staged(StubClient([r1]), make_file_diff("a.py", ["1"]), "m")
    res["answers"]["risk_level"]["score"] = 99
    assert r1["answers"]["risk_level"]["score"] == 0.3


# ---------- CLI ----------

def run_cli(monkeypatch, diff, client, max_lines=250):
    monkeypatch.setattr(
        "systemone_gate.cli.subprocess.check_output", lambda *a, **k: diff
    )
    return cli.handle_diff(client, "m", max_lines=max_lines)


def test_cli_prints_coverage_and_approves(monkeypatch, capsys):
    diff = (
        make_file_diff("package-lock.json", [f"l{i}" for i in range(400)])
        + make_file_diff("img.png", ["x"]).replace("+x", "Binary files a/img.png and b/img.png differ")
        + make_file_diff("big.py", [f"c{i}" for i in range(300)])
        + make_file_diff("ok.py", ["x"])
    )
    client = StubClient()
    code = run_cli(monkeypatch, diff, client, max_lines=100)
    out = capsys.readouterr().out
    assert code == 0
    assert "Files reviewed: 2" in out
    assert "skipped: 2 (lockfile, binary)" in out
    assert "truncated: 1" in out
    assert "package-lock.json" in out and "big.py" in out
    assert out.index("Inspecting") < out.index("Files reviewed") < out.index("Impact Report")
    assert "[APPROVED]" in out


def test_cli_blocks_only_above_thresholds(monkeypatch, capsys):
    diff = make_file_diff("a.py", ["x"])
    code = run_cli(monkeypatch, diff, StubClient([answer(1.9, 0.7, "breaking_change")]))
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().err
    code = run_cli(monkeypatch, diff, StubClient([answer(1.9, 0.6)]))
    assert code == 0
    code = run_cli(monkeypatch, diff, StubClient([answer(1.8, 0.9)]))
    assert code == 0


def test_cli_blocks_when_risky_file_is_after_lockfile(monkeypatch):
    diff = make_file_diff("yarn.lock", ["x"] * 600) + make_file_diff("src/evil.py", ["x"])
    client = StubClient([answer(1.95, 0.9, "breaking_change")])
    assert run_cli(monkeypatch, diff, client) == 1


def test_cli_error_dict_warns_and_exits_zero(monkeypatch, capsys):
    code = run_cli(monkeypatch, make_file_diff("a.py", ["x"]), StubClient([{"error": "offline"}]))
    assert code == 0
    assert "offline" in capsys.readouterr().err


def test_cli_empty_diff(monkeypatch, capsys):
    client = StubClient()
    assert run_cli(monkeypatch, "  \n", client) == 0
    assert client.calls == []
    assert "Commit allowed" in capsys.readouterr().out
