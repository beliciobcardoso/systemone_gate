"""The version must be the same everywhere it is exposed, and documented in the changelog."""

import json
import re
import subprocess
import sys
from pathlib import Path

import systemone_gate

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _pyproject_version() -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert match, "version not found in pyproject.toml"
    return match.group(1)


def _mcp_server_version() -> str:
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
    result = subprocess.run(
        [sys.executable, "-m", "systemone_gate.mcp_server"],
        input=request,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
    )
    reply = json.loads(result.stdout.splitlines()[0])
    return reply["result"]["serverInfo"]["version"]


def test_version_is_semver():
    assert SEMVER.match(systemone_gate.__version__)


def test_package_matches_pyproject():
    assert systemone_gate.__version__ == _pyproject_version()


def test_mcp_server_reports_the_package_version():
    assert _mcp_server_version() == systemone_gate.__version__


def test_changelog_has_a_section_for_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(systemone_gate.__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M)


def test_changelog_keeps_an_unreleased_section_above_the_latest_release():
    headings = re.findall(r"^## \[([^\]]+)\]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert headings[0] == "Unreleased"
    assert headings[1] == systemone_gate.__version__
