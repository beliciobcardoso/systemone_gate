import io
import os
import subprocess
import sys

import pytest

from systemone_gate import cli
from systemone_gate.output import EMOJI_TO_ASCII, PlainWriter, plain_output, to_plain_text

NO_STAGED_MARKER = "Nenhuma alteração staged"


class AsciiStream(io.TextIOWrapper):
    """Text stream that, like a C/POSIX-locale pipe, cannot encode non-ASCII."""

    def __init__(self):
        super().__init__(io.BytesIO(), encoding="ascii", errors="strict")


def _utf8_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8")


def _read(stream):
    stream.flush()
    return stream.buffer.getvalue().decode(stream.encoding)


# ---- translation -----------------------------------------------------------

@pytest.mark.parametrize("symbol", ["✅", "❌", "⚠️", "ℹ️", "🔍", "🩺", "🛡️", "💡", "📁", "📊", "·", "•"])
def test_every_production_symbol_is_mapped_to_ascii(symbol):
    out = to_plain_text(symbol)
    assert out.isascii() and out != ""


def test_known_tokens():
    assert to_plain_text("✅ ok") == "[OK] ok"
    assert to_plain_text("❌ x") == "[ERRO] x"
    assert to_plain_text("⚠️  y") == "[AVISO]  y"


def test_variation_selector_is_dropped_even_alone():
    assert "️" not in to_plain_text("⚠️")
    assert to_plain_text("a️b") == "ab"


def test_text_without_symbols_is_unchanged():
    assert to_plain_text("alteração · ok") .startswith("alteração")


def test_mapping_values_are_ascii():
    assert all(v.isascii() for v in EMOJI_TO_ASCII.values())


# ---- writer ----------------------------------------------------------------

def test_writer_translates_and_forwards_to_target():
    target = _utf8_stream()
    writer = PlainWriter(target)
    assert writer.write("✅ pronto") == len("✅ pronto")
    writer.flush()
    assert _read(target) == "[OK] pronto"


def test_writer_handles_symbol_split_across_partial_writes():
    target = _utf8_stream()
    writer = PlainWriter(target)
    writer.write("⚠")
    writer.write("️ aviso")
    assert _read(target) == "[AVISO] aviso"


def test_writer_rejects_non_str_like_a_text_stream():
    writer = PlainWriter(_utf8_stream())
    with pytest.raises(TypeError):
        writer.write(b"bytes")


def test_writer_flush_delegates():
    class Flushable:
        flushed = False
        encoding = "utf-8"

        def write(self, s):
            return len(s)

        def flush(self):
            self.flushed = True

    target = Flushable()
    PlainWriter(target).flush()
    assert target.flushed


def test_writer_degrades_unencodable_letters_on_ascii_target():
    target = AsciiStream()
    PlainWriter(target).write("✅ alteração")
    assert _read(target) == "[OK] alteracao"


def test_writer_keeps_accents_when_target_can_encode_them():
    target = _utf8_stream()
    PlainWriter(target).write("✅ alteração")
    assert _read(target) == "[OK] alteração"


def test_writer_delegates_other_attributes():
    target = _utf8_stream()
    assert PlainWriter(target).encoding == "utf-8"


# ---- context manager -------------------------------------------------------

def test_plain_output_true_wraps_and_restores(monkeypatch):
    monkeypatch.delenv("SYSTEMONE_PLAIN", raising=False)
    out, err = _utf8_stream(), _utf8_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with plain_output(True):
        assert isinstance(sys.stdout, PlainWriter) and isinstance(sys.stderr, PlainWriter)
        print("✅")
    assert sys.stdout is out and sys.stderr is err
    assert _read(out) == "[OK]\n"


def test_plain_output_restores_on_system_exit(monkeypatch):
    out = _utf8_stream()
    monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit):
        with plain_output(True):
            sys.exit(3)
    assert sys.stdout is out


def test_plain_output_restores_on_exception(monkeypatch):
    out = _utf8_stream()
    monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(RuntimeError):
        with plain_output(True):
            raise RuntimeError("boom")
    assert sys.stdout is out


def test_plain_output_default_leaves_utf8_streams_untouched(monkeypatch):
    monkeypatch.delenv("SYSTEMONE_PLAIN", raising=False)
    out = _utf8_stream()
    monkeypatch.setattr(sys, "stdout", out)
    with plain_output(None):
        assert sys.stdout is out
        print("✅")
    assert _read(out) == "✅\n"


@pytest.mark.parametrize("value, expected_plain", [("1", True), ("yes", True), ("0", False), ("", False)])
def test_plain_output_env_var(monkeypatch, value, expected_plain):
    monkeypatch.setenv("SYSTEMONE_PLAIN", value)
    out = _utf8_stream()
    monkeypatch.setattr(sys, "stdout", out)
    with plain_output(None):
        assert isinstance(sys.stdout, PlainWriter) is expected_plain


def test_plain_output_wraps_only_streams_that_cannot_encode(monkeypatch):
    monkeypatch.delenv("SYSTEMONE_PLAIN", raising=False)
    out, err = _utf8_stream(), AsciiStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with plain_output(None):
        assert sys.stdout is out
        assert isinstance(sys.stderr, PlainWriter)
        print("❌ falha", file=sys.stderr)
    assert sys.stderr is err
    assert _read(err) == "[ERRO] falha\n"


def test_stream_without_encoding_is_treated_as_ascii(monkeypatch):
    monkeypatch.delenv("SYSTEMONE_PLAIN", raising=False)

    class NoEncoding:
        encoding = None

        def write(self, s):
            return len(s)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", NoEncoding())
    with plain_output(None):
        assert isinstance(sys.stdout, PlainWriter)


# ---- CLI integration -------------------------------------------------------

def test_main_plain_flag_translates_and_restores(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(_empty_repo(tmp_path))
    monkeypatch.setenv("OLLAMA_SYSTEMONE_URL", "http://127.0.0.1:9/")
    before = sys.stdout
    with pytest.raises(SystemExit) as e:
        cli.main(["--plain", "diff"])
    assert e.value.code == 0
    assert sys.stdout is before
    out = capsys.readouterr().out
    assert "[INFO]" in out and "ℹ" not in out


def test_main_default_keeps_emoji(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SYSTEMONE_PLAIN", raising=False)
    monkeypatch.chdir(_empty_repo(tmp_path))
    monkeypatch.setenv("OLLAMA_SYSTEMONE_URL", "http://127.0.0.1:9/")
    with pytest.raises(SystemExit):
        cli.main(["diff"])
    assert "ℹ️" in capsys.readouterr().out


def test_mcp_path_is_not_wrapped(monkeypatch):
    seen = {}
    original = sys.stdout

    def fake_server():
        seen["stdout"] = sys.stdout

    monkeypatch.setattr(cli, "run_mcp_server", fake_server)
    cli.main(["--plain", "mcp"])
    assert seen["stdout"] is original
    assert sys.stdout is original


def _empty_repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=_git_env())
    return path


def _git_env():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def _run_cli(repo, repo_root, extra_env, args):
    env = _git_env()
    env.update({"PYTHONPATH": repo_root, "OLLAMA_SYSTEMONE_URL": "http://127.0.0.1:9/"})
    env.update(extra_env)
    return subprocess.run([sys.executable, "-m", "systemone_gate.cli"] + args, cwd=repo,
                          env=env, capture_output=True, timeout=30)


def test_ascii_stream_does_not_crash_and_exit_code_is_unchanged(tmp_path, repo_root):
    repo = _empty_repo(tmp_path)
    out = _run_cli(repo, repo_root, {"PYTHONIOENCODING": "ascii"}, ["diff"])
    assert out.returncode == 0, out.stderr
    text = out.stdout.decode("ascii")
    assert "[INFO]" in text and "Commit liberado" in text
    assert b"Traceback" not in out.stderr


def test_ascii_stream_error_path_keeps_exit_code(tmp_path, repo_root):
    out = _run_cli(tmp_path, repo_root, {"PYTHONIOENCODING": "ascii"}, ["diff"])  # not a git repo
    assert out.returncode == 1
    assert b"[ERRO]" in out.stderr and b"Traceback" not in out.stderr


@pytest.mark.parametrize("args, env", [(["--plain", "diff"], {}), (["diff"], {"SYSTEMONE_PLAIN": "1"})])
def test_plain_forced_on_utf8_stream(tmp_path, repo_root, args, env):
    repo = _empty_repo(tmp_path)
    out = _run_cli(repo, repo_root, dict(env, PYTHONIOENCODING="utf-8"), args)
    assert out.returncode == 0, out.stderr
    text = out.stdout.decode("utf-8")
    assert "[INFO]" in text and "ℹ" not in text
    assert NO_STAGED_MARKER in text  # accents preserved on a UTF-8 stream


def test_default_utf8_output_preserves_emoji(tmp_path, repo_root):
    repo = _empty_repo(tmp_path)
    out = _run_cli(repo, repo_root, {"PYTHONIOENCODING": "utf-8"}, ["diff"])
    assert "ℹ️" in out.stdout.decode("utf-8")
