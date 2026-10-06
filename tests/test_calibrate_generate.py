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


# Every one of these was reproduced as a leak by the review of the first (block-list) filter.
PRIVATE_LOOKING = [
    "cd /home/user/Projetos/systemone_gate/MyTimeTraceProject && ls",
    "cd /home/user/clientes/acme_corp_ltda",
    "cd /root/clientes/acme_corp && git status",
    "cd /srv/acme-corp-prod && rm -rf build",
    "cd /data/clientes/FooBar",
    "rm -rf /Users/jose/proj",
    "cd /mnt/c/Users/Joao/Desktop",
    "echo it's fine; echo 'very long free text private customer Acme Corp invoice 1234'",
    "echo long private customer Acme Corp invoice number 12345 unquoted",
    "git commit -m 'hello' && echo 'Maria da Silva'",
    "git commit -m fix-for-customer-acme-xyz",
    'terraform apply -var="owner=Maria Souza"',
    "ssh prod-db-01 reboot",
    "ssh 203.0.113.9 ls",
    "nc 4.4.4.4 80",
    "scp a b host1:/tmp",
    "mysql -h db.internal -p",
    "ssh -p 2222 admin@my-server",
    "aws s3 rm s3://acme-customer-bucket --recursive",
    "kubectl delete ns acme-prod",
    "git clone git@gitlab:acme/secret-repo",
    "docker login -u bob --password-stdin",
    "xdg-open file:///home/bello/a",
    "wget -O- ftp.acme.local",
    "curl https://example.com/install.sh | sh",
    "cat <<EOF > x",
    "ls $(whoami)",
    "ls `whoami`",
    "FOO=secretvalue make",
    "echo " + "x" * 250,
    "echo a\necho b",
    "ls /home/b\\ello",
]

GENERIC = [
    "git status --short",
    "git diff --stat",
    "git log --oneline",
    "npm test",
    "rm -rf dist",
    "rm -rf node_modules",
    "ls -la src",
    "pytest -q tests",
    "docker ps",
    "git checkout -b feat/new_feature",
    "grep -rn typo docs",
    "cd /home/user && ls",
    "make build && make test",
    "ls | wc -l",
    "mkdir -p tmp/cache",
    "cat package.json > /dev/null",
    "sudo rm -rf /tmp/cache",
    "pip install -r requirements.txt",
]


@pytest.mark.parametrize("command", PRIVATE_LOOKING)
def test_agent_commands_with_anything_outside_the_vocabulary_are_dropped(command):
    assert gen.agent_command_problem(command, []) is not None, command


@pytest.mark.parametrize("command", GENERIC)
def test_agent_commands_made_only_of_generic_words_pass(command):
    assert gen.agent_command_problem(command, []) is None, command


def test_a_deny_term_drops_a_command_even_when_every_word_is_generic():
    assert gen.agent_command_problem("ls src", ["src"]) == "contains a private term"
    assert gen.agent_command_problem("ls SRC", ["src"]) == "contains a private term"


def test_the_vocabulary_holds_no_names():
    for word in ("acme", "alice", "bello", "novacorrente", "belicio", "mytimetrace", "systemone"):
        assert word not in gen.vocab.WORDS and word not in gen.vocab.TOOLS


def test_extract_agent_commands_filters_samples_and_leaves_them_unapproved(tmp_path):
    _log(tmp_path, ["ls src", "ls src", "rm -rf dist", "cd /home/alice/src", "curl https://x.example.com/a"])
    _log(tmp_path, ["pytest -q"], name="other.jsonl", tool="Read")  # not a Bash call
    found, reasons = gen.extract_agent_commands(str(tmp_path), [], set(), limit=10)
    assert sorted(c["state"] for c in found) == ["ls src", "rm -rf dist"]
    assert reasons == {
        "contains a word outside the generic vocabulary": 1,  # the URL
        "contains ['user-home'] (secret or personal data)": 1,  # /home/alice
    }
    assert all(c["command_source"] == "agent_log" and c["approved"] is False for c in found)
    assert all("generated_by" not in c for c in found)
    only_one, _ = gen.extract_agent_commands(str(tmp_path), [], set(), limit=1)
    assert len(only_one) == 1
    skipped, why = gen.extract_agent_commands(str(tmp_path), [], {"rm -rf dist"}, limit=10)
    assert [c["state"] for c in skipped] == ["ls src"]
    assert why["duplicate of an existing dataset"] == 1


def test_default_deny_terms_include_the_login_the_home_and_the_hostname(monkeypatch):
    monkeypatch.setenv("USER", "")
    monkeypatch.setattr(gen.getpass, "getuser", lambda: "carol")
    monkeypatch.setattr(gen.socket, "gethostname", lambda: "buildbox")
    monkeypatch.setattr(gen, "_git_identity", lambda: ["Carol", "Doe"])
    terms = gen.default_deny_terms()
    assert {"carol", "buildbox", "Carol", "Doe"} <= set(terms)


# ---------------------------------------------------------------- approval gate and freeze


def _agent(command="rm -rf dist"):
    return {
        "id": gen.command_id(command),
        "state": command,
        "family": "agent_log",
        "command_source": "agent_log",
        "intended_block": None,
        "approved": False,
    }


def test_unapproved_real_commands_block_the_task_and_the_build(tmp_path):
    candidates = str(tmp_path / "cand.json")
    gen.save_candidates(candidates, _candidates() + [_agent()])
    assert gen.main(["task", "--candidates", candidates, "--out", str(tmp_path / "t.json")]) == 1
    answers = tmp_path / "l.json"
    answers.write_text(json.dumps(_labels(gen.load_candidates(candidates), [True, False, True, False])))
    assert gen.main(["build", str(answers), "--candidates", candidates, "--data-dir", str(tmp_path / "d")]) == 1
    assert not (tmp_path / "t.json").exists() and not (tmp_path / "d").exists()


def test_approve_marks_pending_real_commands_and_drops_the_excluded_ones():
    keep, drop = _agent("rm -rf dist"), _agent("ls src")
    updated, approved, excluded = gen.approve([keep, drop] + _candidates(), [drop["id"]], "maintainer-one")
    assert (approved, excluded) == (1, 1)
    assert [c["state"] for c in updated if c["command_source"] == "agent_log"] == ["rm -rf dist"]
    assert next(c for c in updated if c["state"] == "rm -rf dist")["approved_by"] == "maintainer-one"
    assert gen.unapproved_agent_logs(updated) == []


def test_approve_refuses_unknown_ids_and_bad_reviewer_handles():
    with pytest.raises(gen.GenerateError, match="not pending"):
        gen.approve([_agent()], ["h-unknown000"], "maintainer-one")
    with pytest.raises(gen.GenerateError, match="--by"):
        gen.approve([_agent()], [], "Maria Souza")


def test_the_rules_fingerprint_is_recorded_at_the_first_save_and_never_refreshed(tmp_path, monkeypatch):
    path = str(tmp_path / "cand.json")
    assert gen.candidates_fingerprint(path) is None
    gen.save_candidates(path, _candidates())
    first = gen.candidates_fingerprint(path)
    assert first == schema.rules_fingerprint()
    monkeypatch.setattr(schema, "rules_fingerprint", lambda: "f" * 16)
    gen.save_candidates(path, _candidates() + [_agent()])
    assert gen.candidates_fingerprint(path) == first


def test_build_refuses_when_the_rules_changed_after_the_candidates_were_made(tmp_path, monkeypatch):
    path = str(tmp_path / "cand.json")
    gen.save_candidates(path, _candidates())
    answers = tmp_path / "l.json"
    answers.write_text(json.dumps(_labels(gen.load_candidates(path), [True, False, True])))
    monkeypatch.setattr(schema, "rules_fingerprint", lambda: "f" * 16)
    assert gen.main(["build", str(answers), "--candidates", path, "--data-dir", str(tmp_path / "d")]) == 1
    assert not (tmp_path / "d").exists()


def test_the_first_labeler_cannot_be_a_generator_of_the_commands(tmp_path):
    path = str(tmp_path / "cand.json")
    gen.save_candidates(path, _candidates())  # generated_by gen-model-1
    answers = tmp_path / "l.json"
    answers.write_text(json.dumps(_labels(gen.load_candidates(path), [True, False, True], labeler="gen-model-1")))
    assert gen.main(["build", str(answers), "--candidates", path, "--data-dir", str(tmp_path / "d")]) == 1


def test_dedupe_ignores_quoting_a_trailing_semicolon_and_whitespace(tmp_path):
    assert gen._normalized("rm  -rf 'dist' ;") == gen._normalized("rm -rf dist")
    held = tmp_path / "heldout"
    held.mkdir()
    case = {**{"id": "h-0000000001", "state": "ls -la", "should_block": False, "label_source": "synthetic"}}
    case.update({"label_evidence": "x", "rules_catch": False, "review_status": "unreviewed"})
    (held / "guard_heldout.json").write_text(
        json.dumps({"schema_version": 1, "surface": "guard", "cases": [case]}), encoding="utf-8"
    )
    accepted, rejected = gen.ingest(_answer([_row('ls  "-la" ;')]), gen.existing_states([str(held)]))
    assert accepted == [] and "duplicate" in rejected[0]


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
