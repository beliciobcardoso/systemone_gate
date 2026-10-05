import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # import fake_ollama

from fake_ollama import FakeOllama  # noqa: E402

from systemone_gate.client import SystemOneClient  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def fake_ollama():
    """Factory: each call returns a started FakeOllama; all stopped on teardown."""
    started = []

    def make():
        fake = FakeOllama().start()
        started.append(fake)
        return fake

    yield make
    for fake in started:
        fake.stop()


@pytest.fixture
def fake(fake_ollama):
    return fake_ollama()


@pytest.fixture
def client():
    """Build a SystemOneClient pointing at a given URL."""
    def make(url, **kwargs):
        return SystemOneClient(endpoint=url, **kwargs)
    return make


@pytest.fixture
def closed_port_url():
    """URL of a localhost port that was free and is now closed (connection refused)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return "http://127.0.0.1:%d/v1/systemone" % port


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def sub_env():
    """Environment for subprocesses: no inherited endpoint override."""
    def make(url):
        env = dict(os.environ)
        env["OLLAMA_SYSTEMONE_URL"] = url
        env["PYTHONPATH"] = REPO_ROOT
        return env
    return make
