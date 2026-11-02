import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_constitution", ROOT / "scripts/check_constitution.py"
)
checker = importlib.util.module_from_spec(spec)
sys.modules["check_constitution"] = checker
spec.loader.exec_module(checker)

BASE = """# Test constitution

- 版本：1.0.0
- 最后修订：2026-11-02

## A1. 规格先行

正文。

**强制**：automated — `scripts/check.py`
"""


@pytest.fixture
def fake_root(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/check.py").write_text("# check\n")
    return tmp_path


def test_repository_constitution_passes():
    assert checker.check() == []


def test_repository_declares_every_article_with_enforcement():
    articles = checker.parse_articles((ROOT / "CONSTITUTION.md").read_text())
    assert len(articles) >= 10
    for article in articles:
        assert checker.enforcement(article), article.article_id


def test_document_requires_version_and_date():
    errors = checker.check_document("# 宪法\n\n## A1. 规则\n\n**强制**：review — `README.md`\n")
    assert any("版本" in error for error in errors)
    assert any("最后修订" in error for error in errors)


def test_duplicate_article_ids_fail(fake_root):
    text = BASE + "\n## A1. 重复\n\n**强制**：automated — `scripts/check.py`\n"
    errors = checker.check_document(text, fake_root)
    assert any("duplicate article A1" in error for error in errors)


def test_unknown_enforcement_kind_fails(fake_root):
    text = BASE.replace("automated", "whenever")
    errors = checker.check_document(text, fake_root)
    assert any("unknown enforcement kind" in error for error in errors)


def test_missing_enforcement_target_fails(fake_root):
    text = BASE.replace("`scripts/check.py`", "`scripts/missing.py`")
    errors = checker.check_document(text, fake_root)
    assert any("missing enforcement target" in error for error in errors)


def test_article_without_enforcement_fails(fake_root):
    text = BASE.split("**强制**")[0]
    errors = checker.check_document(text, fake_root)
    assert any("missing `**强制**`" in error for error in errors)


@pytest.mark.parametrize(
    "source",
    [
        "exec(code)",
        "eval(source)",
        "compile(source, '<strategy>', 'exec')",
        "import builtins; builtins.exec(code)",
    ],
)
def test_interpreter_calls_are_flagged(source):
    assert checker.dynamic_execution_violations(source, "qtrade_api.routes")


@pytest.mark.parametrize(
    "source",
    [
        "import re; re.compile(r'x')",
        "pattern.compile(value)",
        "runner.execute(code)",
    ],
)
def test_non_interpreter_calls_are_allowed(source):
    assert checker.dynamic_execution_violations(source, "qtrade_application.backtesting") == []


def test_runner_packages_may_interpret_strategy_source():
    errors = checker.check_dynamic_execution(ROOT)
    assert errors == []


@pytest.mark.parametrize(
    "source",
    [
        "import passlib",
        "from bcrypt import checkpw",
        "from argon2 import PasswordHasher",
        "verify_password(stored, supplied)",
        "auth.check_password(raw)",
    ],
)
def test_local_password_auth_is_flagged(source):
    assert checker.password_auth_violations(source, "qtrade_api.auth")


@pytest.mark.parametrize(
    "source",
    [
        "import cryptography",
        "from jose import jwt",
        "hashed = sha256(raw)",
        "token.check_expiry()",
    ],
)
def test_sso_building_blocks_are_allowed(source):
    assert checker.password_auth_violations(source, "qtrade_api.auth") == []


def test_secret_detection_flags_provider_keys():
    assert checker.detect_secrets("key = 'AKIA" + "IOSFODNN7EXAMPLE'") == ["aws access key"]
    assert checker.detect_secrets("token: ghp_" + "a" * 36) == ["github token"]
    assert checker.detect_secrets(
        "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    ) == ["private key block"]


def test_secret_detection_ignores_placeholders():
    assert checker.detect_secrets("AKIA...<redacted>") == []
    assert checker.detect_secrets("password=<redacted> token=<token>") == []


def test_repository_has_no_tracked_credentials():
    assert checker.check_tracked_secrets(ROOT) == []


@pytest.mark.parametrize(
    "paths,messages,env,expected",
    [
        (["apps/api/src/qtrade_api/main.py"], [], {}, True),
        (["apps/api/src/qtrade_api/main.py", "openspec/changes/x/spec.md"], [], {}, False),
        (["docs/readme.md"], [], {}, False),
        (["apps/api/src/qtrade_api/main.py"], ["refactor [no-spec-change]"], {}, False),
        (["apps/api/src/qtrade_api/main.py"], [], {"QTRADE_CONSTITUTION_SKIP_SPEC": "1"}, False),
        (["packages/domain/src/qtrade_domain/risk.py"], [], {}, True),
    ],
)
def test_spec_change_requirement(paths, messages, env, expected):
    assert checker.spec_change_required(paths, messages, env) is expected


def test_pull_request_requires_openspec_delta():
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        pytest.skip("not running on a pull request")
    base = os.environ.get("GITHUB_BASE_REF", "")
    if not base:
        pytest.skip("GITHUB_BASE_REF is not set")
    errors = checker.check_spec_diff(ROOT, base)
    if any("cannot resolve base ref" in error for error in errors):
        pytest.skip(f"base ref {base!r} is not fetched in this checkout")
    assert errors == []
