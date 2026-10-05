"""Contract test against the REAL local Ollama; skipped automatically when unreachable."""

import pytest

from systemone_gate.client import DEFAULT_ENDPOINT
from systemone_gate.doctor import FAIL, derive_base_url, run_doctor

pytestmark = pytest.mark.contract

PROBE_TIMEOUT = 2.0
SMOKE_TIMEOUT = 120.0  # first call may load the model


@pytest.fixture(scope="module")
def real_report():
    probe = run_doctor(DEFAULT_ENDPOINT, smoke=False, timeout=PROBE_TIMEOUT)
    if probe.checks[0].status == FAIL:
        pytest.skip("Ollama indisponível em %s" % derive_base_url(DEFAULT_ENDPOINT))
    return run_doctor(DEFAULT_ENDPOINT, timeout=PROBE_TIMEOUT, smoke_timeout=SMOKE_TIMEOUT)


def test_real_ollama_satisfies_contract(real_report):
    failures = [c for c in real_report.checks if c.status == FAIL]
    assert not failures, "; ".join("%s: %s" % (c.name, c.message) for c in failures)
