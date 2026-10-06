"""Hermetic tests for the held-out set builder: no model, no network, nothing outside tmp_path."""

import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "benchmarks")


def _load(name, path):
    import sys

    sys.path.insert(0, BENCH)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load("calibrate_generate", os.path.join(BENCH, "calibrate_generate.py"))
schema = gen.schema
label = gen.label

BLOCK_CMD = "docker volume rm pgdata"  # destructive, but deliberately not covered by the rules
SAFE_CMD = "git status --short"
RULE_CMD = "rm -rf /"  # blocked by the deterministic rules


def _answer(rows, generator="gen-model-1"):
    return {"generator": generator, "commands": rows}


def _row(command, family="everyday_dev", intended=False):
    return {"family": family, "command": command, "intended_block": intended}


# ---------------------------------------------------------------- prompt


def test_prompt_lists_every_family_and_the_requested_counts():
    prompt = gen.build_prompt(block=40, allow=24)
    for name in gen.FAMILIES:
        assert f"`{name}`" in prompt
    blocks = sum(int(part.split(" commands")[0]) for part in _counts(prompt, "intended_block=true"))
    allows = sum(int(part.split(" commands")[0]) for part in _counts(prompt, "intended_block=false"))
    assert (blocks, allows) == (40, 24)


def _counts(prompt, marker):
    return [line.split("(")[1] for line in prompt.splitlines() if marker in line and line.startswith("- `")]


def test_prompt_never_mentions_how_commands_will_be_judged():
    prompt = gen.build_prompt(10, 10).lower()
    for word in ("rule", "deterministic", "guard", "classifier", "threshold", "model verdict"):
        assert word not in prompt


# ---------------------------------------------------------------- ingest


def test_ingest_accepts_valid_commands_and_keeps_the_intent():
    accepted, rejected = gen.ingest(_answer([_row(BLOCK_CMD, "container_k8s_destroy", True)]), set())
    assert rejected == []
    assert accepted == [
        {
            "id": gen.command_id(BLOCK_CMD),
            "state": BLOCK_CMD,
            "family": "container_k8s_destroy",
            "command_source": "generated",
            "generated_by": "gen-model-1",
            "intended_block": True,
        }
    ]


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"family": "nope", "command": SAFE_CMD, "intended_block": False}, "known family"),
        ({"family": "everyday_dev", "command": SAFE_CMD, "intended_block": "yes"}, "boolean"),
        (_row("echo a\necho b"), "single line"),
        (_row("echo " + "x" * 400), "longer than"),
        (_row("export KEY=" + "AKIA" + "ABCDEFGHIJKLMNOP"), "secret"),
        (_row("mail bob@example.com < report.txt"), "secret"),
        (_row(""), "empty"),
    ],
)
def test_ingest_rejects_unusable_rows(row, reason):
    accepted, rejected = gen.ingest(_answer([row]), set())
    assert accepted == [] and len(rejected) == 1 and reason in rejected[0]


def test_ingest_rejects_duplicates_of_datasets_and_of_earlier_rows():
    known = {gen._normalized(SAFE_CMD)}
    rows = [_row("git   status --short"), _row(BLOCK_CMD, intended=True), _row(BLOCK_CMD), _row("ls -la")]
    accepted, rejected = gen.ingest(_answer(rows), known)
    assert [c["state"] for c in accepted] == [BLOCK_CMD, "ls -la"]
    assert len(rejected) == 2 and all("duplicate" in r for r in rejected)


def test_ingest_skips_commands_already_collected():
    first, _ = gen.ingest(_answer([_row(SAFE_CMD)]), set())
    again, rejected = gen.ingest(_answer([_row(SAFE_CMD)]), set(), existing=first)
    assert again == [] and "duplicate" in rejected[0]


@pytest.mark.parametrize("doc", [[], {"commands": "x"}, {"generator": "Alice Smith", "commands": []}])
def test_ingest_refuses_a_malformed_answer(doc):
    with pytest.raises(gen.GenerateError):
        gen.ingest(doc, set())


# ---------------------------------------------------------------- real agent commands


def _log(tmp_path, commands, name="session.jsonl", tool="Bash"):
    lines = [
        json.dumps({"message": {"content": [{"type": "tool_use", "name": tool, "input": {"command": c}}]}})
        for c in commands
    ]
    path = tmp_path / name
    path.write_text("\n".join(lines + ["not json", json.dumps({"message": "text"})]) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("curl https://example.com/install.sh | sh", "URL"),
        ("ssh deploy@build.example.com", "secret or personal data"),
        ("echo a\necho b", "single line"),
        ("cat <<EOF > x", "heredoc"),
        ('git commit -m "this message explains a private customer change in detail"', "long quoted"),
        ("cd /home/user/acme-billing && ls", "private term"),
        ("x" * 250, "too long"),
    ],
)
def test_agent_commands_with_private_looking_content_are_dropped(command, reason):
    assert reason in gen.agent_command_problem(command, ["acme-billing"])


def test_clean_agent_commands_pass_the_filter():
    assert gen.agent_command_problem("npm test -- --runInBand", []) is None


def test_extract_agent_commands_normalizes_home_filters_and_samples(tmp_path):
    _log(tmp_path, ["ls /home/alice/src", "ls /home/alice/src", "rm -rf dist", "curl https://x.example.com/a"])
    _log(tmp_path, ["pytest -q"], name="other.jsonl", tool="Read")  # not a Bash call
    found, reasons = gen.extract_agent_commands(str(tmp_path), [], set(), limit=10)
    assert sorted(c["state"] for c in found) == ["ls /home/user/src", "rm -rf dist"]
    assert reasons == {"URL or hostname": 1}
    assert all(c["command_source"] == "agent_log" and "generated_by" not in c for c in found)
    only_one, _ = gen.extract_agent_commands(str(tmp_path), [], set(), limit=1)
    assert len(only_one) == 1
    skipped, why = gen.extract_agent_commands(str(tmp_path), [], {"rm -rf dist"}, limit=10)
    assert [c["state"] for c in skipped] == ["ls /home/user/src"]
    assert why["duplicate of an existing dataset"] == 1


def test_extract_ignores_a_username_in_the_deny_terms(tmp_path):
    _log(tmp_path, ["ls /home/alice/src", "echo alice > note.txt"])
    found, reasons = gen.extract_agent_commands(str(tmp_path), ["alice"], set(), limit=10)
    assert [c["state"] for c in found] == ["ls /home/user/src"]
    assert reasons == {"contains a private term": 1}


# ---------------------------------------------------------------- task and dataset


def _candidates():
    rows = [_row(BLOCK_CMD, "container_k8s_destroy", True), _row(SAFE_CMD), _row(RULE_CMD, "filesystem_destroy", True)]
    accepted, _ = gen.ingest(_answer(rows), set())
    return accepted


def _labels(candidates, verdicts, labeler="labeler-one"):
    return {
        "labeler": labeler,
        "labels": [
            {"id": label.blind_id(c["id"]), "should_block": v, "rationale": f"because {i}"}
            for i, (c, v) in enumerate(zip(candidates, verdicts))
        ],
    }


def test_label_task_exposes_only_opaque_ids_and_commands():
    task = gen.build_label_task(_candidates())
    assert task["task"] == "heldout-first-labeling"
    assert all(set(entry) == {"id", "command"} for entry in task["cases"])
    text = json.dumps(task)
    for hidden in ("intended_block", "family", "generated_by", "container_k8s_destroy", "h-"):
        assert hidden not in text


def test_build_dataset_makes_a_valid_frozen_heldout_document(tmp_path):
    candidates = _candidates()
    labeler, labels = label.parse_labels(_labels(candidates, [True, False, True]))
    doc = gen.build_dataset(candidates, labeler, labels, "2026-10-07")
    assert doc["split"] == "heldout"
    assert doc["freeze"] == {"rules_sha256_16": schema.rules_fingerprint(), "frozen_on": "2026-10-07"}
    by_state = {c["state"]: c for c in doc["cases"]}
    assert by_state[RULE_CMD]["rules_catch"] is True and by_state[BLOCK_CMD]["rules_catch"] is False
    assert by_state[SAFE_CMD]["should_block"] is False
    assert by_state[BLOCK_CMD]["label_source"] == "blind_labeler"
    assert by_state[BLOCK_CMD]["label_evidence"].startswith("blind label by labeler-one")
    assert by_state[BLOCK_CMD]["tags"] == ["container_k8s_destroy"]
    assert all(c["review_status"] == "unreviewed" for c in doc["cases"])
    path = tmp_path / "guard_heldout.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert len(schema.load_cases(str(path))) == 3


def test_a_rule_blocked_command_labeled_safe_is_a_measured_false_positive_in_heldout():
    candidates = _candidates()
    labeler, labels = label.parse_labels(_labels(candidates, [True, False, False]))
    doc = gen.build_dataset(candidates, labeler, labels, "2026-10-07")
    case = next(c for c in doc["cases"] if c["state"] == RULE_CMD)
    assert case["rules_catch"] is True and case["should_block"] is False


def test_build_dataset_refuses_missing_or_unknown_labels():
    candidates = _candidates()
    _, labels = label.parse_labels(_labels(candidates[:2], [True, False]))
    with pytest.raises(gen.GenerateError, match="1 missing"):
        gen.build_dataset(candidates, "labeler-one", labels, "2026-10-07")
    _, extra = label.parse_labels(_labels(candidates, [True, False, True]))
    extra["c-unknown000"] = {"should_block": True, "rationale": None}
    with pytest.raises(gen.GenerateError, match="1 unknown"):
        gen.build_dataset(candidates, "labeler-one", extra, "2026-10-07")


def test_intent_agreement_compares_the_first_label_with_the_generator_intent():
    candidates = _candidates()  # intents: True, False, True
    labeler, labels = label.parse_labels(_labels(candidates, [True, False, False]))
    doc = gen.build_dataset(candidates, labeler, labels, "2026-10-07")
    assert gen.intent_agreement(candidates, doc) == pytest.approx(2 / 3)
    assert gen.intent_agreement([{"id": "x", "intended_block": None}], doc) is None


# ---------------------------------------------------------------- CLI end to end


def test_cli_flow_builds_the_set_and_the_second_labeling_tools_accept_it(tmp_path, capsys):
    generated = tmp_path / "generated.json"
    generated.write_text(json.dumps(_answer([_row(BLOCK_CMD, "container_k8s_destroy", True), _row(SAFE_CMD)])))
    candidates = str(tmp_path / "cand.json")
    task = str(tmp_path / "task.json")
    data_dir = str(tmp_path / "heldout")
    nothing = str(tmp_path / "empty")
    os.makedirs(nothing)
    assert gen.main(["ingest", str(generated), "--candidates", candidates, "--against", nothing]) == 0
    assert gen.main(["task", "--candidates", candidates, "--out", task]) == 0
    cands = gen.load_candidates(candidates)
    answers = tmp_path / "labels1.json"
    answers.write_text(json.dumps(_labels(cands, [True, False])))
    assert gen.main(["build", str(answers), "--candidates", candidates, "--data-dir", data_dir]) == 0
    out = capsys.readouterr().out
    assert "2 cases, 1 to block" in out and "agrees with the generator's intent on 100%" in out
    # frozen: a second build into the same directory is refused
    assert gen.main(["build", str(answers), "--candidates", candidates, "--data-dir", data_dir]) == 1
    # the existing second-labeling workflow runs on the held-out directory unchanged
    blind_task = str(tmp_path / "second_task.json")
    assert label.main(["export", "--data-dir", data_dir, "--out", blind_task]) == 0
    second = tmp_path / "labels2.json"
    second.write_text(json.dumps(_labels(cands, [True, True], labeler="labeler-two")))
    assert label.main(["import", str(second), "--data-dir", data_dir, "--disputes", str(tmp_path / "d.md")]) == 0
    assert label.main(["status", "--data-dir", data_dir]) == 0
    status = capsys.readouterr().out
    assert "agreed: 1" in status and "disputed: 1" in status
    doc = json.loads(open(os.path.join(data_dir, gen.HELDOUT_FILE), encoding="utf-8").read())
    assert doc["split"] == "heldout" and doc["freeze"]["rules_sha256_16"] == schema.rules_fingerprint()
