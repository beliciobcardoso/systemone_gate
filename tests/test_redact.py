import dataclasses
import time

import pytest

from systemone_gate.redact import RedactionResult, redact_secrets

# Fake secrets are assembled at test time so no real-looking token sits verbatim in the repo.
AWS_ID = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
AWS_ID_STS = "AS" + "IA" + "ABCDEFGHIJKLMNOP"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG" + "/bPxRfiCYEXAMPLEKEY"
GH_TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GH_PAT = "github" + "_pat_" + "11ABCDEFG0abcdefghij_KLMNOPQRSTUVWXYZ0123456789"
SLACK = "xo" + "xb-" + "1234567890-abcdefghijklmnop"
GOOGLE = "AI" + "za" + "SyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q"
STRIPE = "sk" + "_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
STRIPE_RK = "rk" + "_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
JWT = "ey" + "JhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
PEM = ("-----BEGIN RSA PRIVATE " + "KEY-----\nMIIEowIBAAKCAQEA7x9fakefakefake\nabcdefg==\n"
       "-----END RSA PRIVATE " + "KEY-----")

SECRET_CASES = [
    ("aws_access_key", "id = " + AWS_ID + " end", AWS_ID),
    ("aws_access_key", "sts " + AWS_ID_STS, AWS_ID_STS),
    ("aws_secret_key", "aws_secret_access_key = " + AWS_SECRET, AWS_SECRET),
    ("aws_secret_key", 'AWS_SECRET_ACCESS_KEY="' + AWS_SECRET + '"', AWS_SECRET),
    ("github_token", "+token " + GH_TOKEN, GH_TOKEN),
    ("github_token", "x " + GH_TOKEN.replace("ghp_", "gho_"), GH_TOKEN.replace("ghp_", "gho_")),
    ("github_token", "x " + GH_TOKEN.replace("ghp_", "ghs_"), GH_TOKEN.replace("ghp_", "ghs_")),
    ("github_token", "x " + GH_TOKEN.replace("ghp_", "ghu_"), GH_TOKEN.replace("ghp_", "ghu_")),
    ("github_token", "pat " + GH_PAT, GH_PAT),
    ("slack_token", "SLACK " + SLACK, SLACK),
    ("google_api_key", "key=" + GOOGLE, GOOGLE),
    ("stripe_key", "stripe " + STRIPE, STRIPE),
    ("stripe_key", "stripe " + STRIPE_RK, STRIPE_RK),
    ("private_key", "before\n" + PEM + "\nafter", "MIIEowIBAAKCAQEA7x9fakefakefake"),
    (
        "private_key",
        "before\n-----BEGIN OPENSSH PRIVATE " + "KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n",
        "b3BlbnNzaC1rZXktdjEAAAAA",
    ),
    ("jwt", "t=" + JWT, JWT),
    ("bearer_token", "Authorization: Bearer abcDEF123456.tokenvalue-xyz", "abcDEF123456.tokenvalue-xyz"),
    ("bearer_token", "-H 'authorization: bearer Zm9vYmFyYmF6cXV4'", "Zm9vYmFyYmF6cXV4"),
    ("url_password", "postgres://admin:Sup3rS3cret@db.example.com:5432/app", "Sup3rS3cret"),
    ("secret_assignment", "DB_PASSWORD=hunter2hunter2", "hunter2hunter2"),
    ("secret_assignment", 'api_key = "abcd1234efgh5678"', "abcd1234efgh5678"),
    ("secret_assignment", '"password": "correct horse battery"', "correct horse battery"),
    ("secret_assignment", "client_secret: 'zxcvbnm12345'", "zxcvbnm12345"),
    ("secret_assignment", "API-KEY: abcd1234efgh", "abcd1234efgh"),
    ("secret_assignment", "access_token: abcd1234efgh5678", "abcd1234efgh5678"),
    ("secret_assignment", "passwd=letmein123", "letmein123"),
    ("secret_assignment", "SECRET_KEY=django-insecure-abcdef", "django-insecure-abcdef"),
]


@pytest.mark.parametrize("rule,text,secret", SECRET_CASES)
def test_secret_is_redacted(rule, text, secret):
    res = redact_secrets(text)
    assert isinstance(res, RedactionResult)
    assert secret not in res.text
    assert "[REDACTED:%s]" % rule in res.text
    assert rule in res.findings
    assert all(secret not in f for f in res.findings)


def test_surrounding_text_is_preserved():
    res = redact_secrets("+DB_PASSWORD=hunter2hunter2\n+DB_HOST=localhost\n")
    assert res.text == "+DB_PASSWORD=[REDACTED:secret_assignment]\n+DB_HOST=localhost\n"


def test_url_keeps_user_and_host():
    res = redact_secrets("postgres://admin:Sup3rS3cret@db.example.com/app")
    assert res.text == "postgres://admin:[REDACTED:url_password]@db.example.com/app"


def test_bearer_keeps_header_name():
    res = redact_secrets("Authorization: Bearer abcDEF123456.tokenvalue")
    assert res.text == "Authorization: Bearer [REDACTED:bearer_token]"


def test_truncated_pem_block_is_redacted_to_the_end():
    res = redact_secrets("a\n-----BEGIN PRIVATE " + "KEY-----\nMIIEvQIBADANBgkqhkiG9w0B")
    assert res.text == "a\n[REDACTED:private_key]"


def test_findings_count_every_redaction():
    res = redact_secrets("a=" + AWS_ID + " b=" + AWS_ID_STS)
    assert res.findings == ("aws_access_key", "aws_access_key")


NEGATIVE = [
    "commit 3f786850e387550fdab836ed7e6dc881de23001b",
    "index 1f2e3d4..5a6b7c8 100644",
    "data:image/png;base64," + "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ" * 5,
    "password=short",
    "password: abc",
    "the password must be rotated every 90 days",
    "Reset your password and token policy",
    "tokenizer = AutoTokenizer.from_pretrained(name)",
    "tokenizer_config=something_long_here",
    "id = 550e8400-e29b-41d4-a716-446655440000",
    "password = getPassword(request)",
    "token = request.token",
    "token=self.token_value",
    "password: ${DB_PASSWORD}",
    "password = $DB_PASSWORD_VAR",
    "secret = required_value",
    "api_key: required",
    "AKIA is the prefix",
    "see https://example.com:8080/path and http://localhost:11434/v1",
    "git clone ssh://git@github.com:22/org/repo.git",
    "Authorization: Bearer short",
    "eyJ is not a jwt",
    "def f(password, token):\n    return password",
]


@pytest.mark.parametrize("text", NEGATIVE)
def test_normal_text_is_left_alone(text):
    res = redact_secrets(text)
    assert res.text == text
    assert res.findings == ()


@pytest.mark.parametrize("rule,text,secret", SECRET_CASES)
def test_idempotent(rule, text, secret):
    once = redact_secrets(text)
    twice = redact_secrets(once.text)
    assert twice.text == once.text
    assert twice.findings == ()


def test_empty_string():
    assert redact_secrets("") == RedactionResult(text="", findings=())


def test_non_str_raises_type_error():
    with pytest.raises(TypeError):
        redact_secrets(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        redact_secrets(b"bytes")  # type: ignore[arg-type]


def test_result_is_frozen():
    res = redact_secrets("x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.text = "y"  # type: ignore[misc]


MB = 1024 * 1024
ADVERSARIAL = [
    "a" * MB,
    " " * MB,
    "eyJ-" * (MB // 4),
    "eyJ" + "a" * MB,
    "ghp_" * (MB // 4),
    "AKIA" * (MB // 4),
    "-----BEGIN PRIVATE KEY-----" * (MB // 27),
    "password=" * (MB // 9),
    "password=" + "a" * MB,
    'password="' * (MB // 10),
    "password: '" + "a" * MB,
    "://a:" * (MB // 5),
    "http://a:" + "b" * MB,
    "Authorization: " * (MB // 15),
    "Authorization:" + " " * MB,
    "aws_secret_access_key=" * (MB // 22),
    "token=" + "(" * MB,
    "\n".join(["x=1"] * (MB // 4)),
]


@pytest.mark.parametrize("text", ADVERSARIAL, ids=lambda t: "%s...(%d)" % (t[:14].replace("\n", " "), len(t)))
def test_linear_time_on_adversarial_input(text):
    start = time.perf_counter()
    redact_secrets(text)
    assert time.perf_counter() - start < 3.0


def test_does_not_mutate_input_string_value():
    text = "DB_PASSWORD=hunter2hunter2"
    copy = str(text)
    redact_secrets(text)
    assert text == copy
