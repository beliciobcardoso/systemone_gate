"""
ASCII-safe terminal output.

Production strings use emoji. A pipe or terminal whose encoding cannot represent
them (``PYTHONIOENCODING=ascii``, a C/POSIX locale) would crash ``print`` with
``UnicodeEncodeError`` -- inside the pre-commit hook that traceback reads as a
block. ``plain_output`` wraps stdout/stderr with a translating writer for the
duration of a block instead of touching every ``print``.
"""

import os
import sys
import unicodedata
from contextlib import contextmanager
from typing import Any, Iterator, Optional

PLAIN_ENV_VAR = "SYSTEMONE_PLAIN"
VARIATION_SELECTOR = "️"
PROBE_SYMBOL = "✅"

EMOJI_TO_ASCII = {
    "✅": "[OK]",
    "❌": "[ERRO]",
    "⚠": "[AVISO]",
    "ℹ": "[INFO]",
    "🔍": "[INSPECAO]",
    "🩺": "[TRIAGEM]",
    "🛡": "[GUARD]",
    "💡": "[DICA]",
    "📁": "[ARQUIVOS]",
    "📊": "[RISCO]",
    "·": "-",
    "•": "*",
}
_TRANSLATION = str.maketrans({**EMOJI_TO_ASCII, VARIATION_SELECTOR: ""})


def to_plain_text(text: str) -> str:
    """Replaces known symbols with ASCII tokens and drops U+FE0F."""
    return text.translate(_TRANSLATION)


def _degrade(text: str) -> str:
    """Last resort for letters the stream cannot encode: strip accents, then '?'."""
    decomposed = unicodedata.normalize("NFKD", text)
    base = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return base.encode("ascii", errors="replace").decode("ascii")


def _can_encode(text: str, encoding: Optional[str]) -> bool:
    try:
        text.encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


class PlainWriter:
    """Thin text-stream wrapper that translates symbols before writing.

    Stateless per character, so a symbol split across partial writes
    (``"⚠"`` then ``"\\ufe0f"``) is still translated correctly.
    """

    def __init__(self, target: Any):
        self._target = target

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError(f"write() argument must be str, not {type(text).__name__}")
        plain = to_plain_text(text)
        if not _can_encode(plain, getattr(self._target, "encoding", None)):
            plain = _degrade(plain)
        self._target.write(plain)
        return len(text)

    def flush(self) -> None:
        self._target.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


def _env_forces_plain() -> bool:
    value = os.environ.get(PLAIN_ENV_VAR, "")
    return value != "" and value != "0"


def _needs_plain(stream: Any, forced: bool) -> bool:
    return forced or not _can_encode(PROBE_SYMBOL, getattr(stream, "encoding", None))


@contextmanager
def plain_output(plain: Optional[bool]) -> Iterator[None]:
    """Wraps stdout/stderr that need it; always restores the originals.

    Plain is forced by ``plain=True`` or ``SYSTEMONE_PLAIN`` (non-empty, not "0");
    otherwise each stream is wrapped only if its encoding cannot encode emoji.
    """
    forced = plain is True or _env_forces_plain()
    original_out, original_err = sys.stdout, sys.stderr
    try:
        if _needs_plain(original_out, forced):
            sys.stdout = PlainWriter(original_out)
        if _needs_plain(original_err, forced):
            sys.stderr = PlainWriter(original_err)
        yield
    finally:
        sys.stdout, sys.stderr = original_out, original_err
