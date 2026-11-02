"""Enforce QTrade's constitution: document structure plus machine-checkable articles."""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTITUTION = ROOT / "CONSTITUTION.md"

ARTICLE_RE = re.compile(r"^## ([A-Z]\d{1,2})[.、 ]\s*(.+?)\s*$")
VERSION_RE = re.compile(r"^- 版本：(\d+)\.(\d+)\.(\d+)$", re.MULTILINE)
AMENDED_RE = re.compile(r"^- 最后修订：(\d{4}-\d{2}-\d{2})$", re.MULTILINE)
ENFORCEMENT_RE = re.compile(r"^\*\*强制\*\*：(.*)$", re.MULTILINE)
REFERENCE_RE = re.compile(r"`([^`]+)`")
ENFORCEMENT_KINDS = ("automated", "review")

# User strategy source may only be interpreted inside the isolated runner.
RUNNER_PATHS = ("apps/runner/", "adapters/runner/")
INTERPRETER_CALLS = {"exec", "eval", "compile"}

# QTrade delegates identity to SSO; local password handling is forbidden.
PASSWORD_MODULES = {"passlib", "bcrypt", "argon2", "argon2_cffi", "python-jose"}
PASSWORD_IDENTIFIERS = {
    "verify_password",
    "check_password",
    "hash_password",
    "password_hash",
    "pwd_context",
}

SECRET_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
)
FORBIDDEN_TRACKED = (".env", ".pem", ".key", ".p12", ".pfx")
TRACKED_ALLOWED = (".env.example",)
MAX_SCAN_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Article:
    article_id: str
    title: str
    body: str


def parse_articles(text: str) -> list[Article]:
    articles: list[Article] = []
    current: tuple[str, str, list[str]] | None = None
    for line in text.splitlines():
        match = ARTICLE_RE.match(line)
        if match:
            if current is not None:
                articles.append(Article(current[0], current[1], "\n".join(current[2])))
            current = (match.group(1), match.group(2), [])
        elif line.startswith("## "):
            if current is not None:
                articles.append(Article(current[0], current[1], "\n".join(current[2])))
            current = None
        elif current is not None:
            current[2].append(line)
    if current is not None:
        articles.append(Article(current[0], current[1], "\n".join(current[2])))
    return articles


def enforcement(article: Article) -> str | None:
    match = ENFORCEMENT_RE.search(article.body)
    return match.group(1).strip() if match else None


def references(article: Article) -> list[str]:
    line = enforcement(article)
    return REFERENCE_RE.findall(line or "")


def check_document(text: str, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    version = VERSION_RE.search(text)
    if version is None:
        errors.append("constitution: missing `- 版本：MAJOR.MINOR.PATCH`")
    if AMENDED_RE.search(text) is None:
        errors.append("constitution: missing `- 最后修订：YYYY-MM-DD`")

    articles = parse_articles(text)
    if not articles:
        errors.append("constitution: no articles found")
        return errors

    seen: set[str] = set()
    for article in articles:
        label = article.article_id
        if label in seen:
            errors.append(f"constitution: duplicate article {label}")
        seen.add(label)
        if not article.title:
            errors.append(f"constitution {label}: missing title")
        rule = enforcement(article)
        if rule is None:
            errors.append(f"constitution {label}: missing `**强制**` line")
            continue
        kind = rule.split(maxsplit=1)[0].rstrip("：:")
        if kind not in ENFORCEMENT_KINDS:
            errors.append(f"constitution {label}: unknown enforcement kind {kind!r}")
        paths = references(article)
        if not paths:
            errors.append(f"constitution {label}: enforcement names no reference path")
        for reference in paths:
            if not _reference_exists(reference, root):
                errors.append(f"constitution {label}: missing enforcement target {reference}")
    return errors


def _reference_exists(reference: str, root: Path) -> bool:
    if any(char in reference for char in "*?["):
        return any(root.glob(reference))
    if reference.endswith("/"):
        return (root / reference).is_dir()
    return (root / reference).is_file()


def dynamic_execution_violations(source: str, module: str) -> list[str]:
    """Reject interpreter calls on strategy source outside the isolated runner."""
    errors: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_builtin = isinstance(func, ast.Name) and func.id in INTERPRETER_CALLS
        is_builtins = (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "builtins"
            and func.attr in INTERPRETER_CALLS
        )
        if is_builtin or is_builtins:
            errors.append(f"{module}:{node.lineno}: interpreter call outside the runner")
    return errors


def check_dynamic_execution(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for area in ("apps", "packages", "adapters"):
        for path in (root / area).glob("*/src/**/*.py"):
            relative = path.relative_to(root).as_posix()
            if relative.startswith(RUNNER_PATHS):
                continue
            parts = path.relative_to(root).parts
            index = parts.index("src")
            module = ".".join((*parts[index + 1 : -1], path.stem))
            errors.extend(dynamic_execution_violations(path.read_text(), module))
    return errors


def password_auth_violations(source: str, module: str) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name.split(".")[0] in PASSWORD_MODULES:
                    errors.append(f"{module}:{node.lineno}: local password auth {item.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in PASSWORD_MODULES:
                errors.append(f"{module}:{node.lineno}: local password auth {node.module}")
        elif isinstance(node, ast.Name) and node.id in PASSWORD_IDENTIFIERS:
            errors.append(f"{module}:{node.lineno}: local password auth {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in PASSWORD_IDENTIFIERS:
            errors.append(f"{module}:{node.lineno}: local password auth {node.attr}")
    return errors


def check_password_auth(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for area in ("apps", "packages", "adapters"):
        for path in (root / area).glob("*/src/**/*.py"):
            parts = path.relative_to(root).parts
            index = parts.index("src")
            module = ".".join((*parts[index + 1 : -1], path.stem))
            errors.extend(password_auth_violations(path.read_text(), module))
    return errors


def detect_secrets(text: str) -> list[str]:
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    return [root / name for name in names]


def check_tracked_secrets(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        files = _tracked_files(root)
    except (OSError, RuntimeError) as exc:
        return [f"constitution: cannot enumerate tracked files: {exc}"]
    for path in files:
        name = path.name.lower()
        if name.endswith(FORBIDDEN_TRACKED) and path.name not in TRACKED_ALLOWED:
            errors.append(f"constitution: tracked credential file {path.relative_to(root)}")
            continue
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for label in detect_secrets(data.decode("utf-8", "replace")):
            errors.append(f"constitution: {label} in {path.relative_to(root)}")
    return errors


def check_toolchain(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    pyproject = (root / "pyproject.toml").read_text()
    if 'requires-python = ">=3.12,<3.13"' not in pyproject:
        errors.append("constitution: Python must stay pinned to 3.12")
    package = (root / "apps/web/package.json").read_text()
    if '"node": ">=22' not in package:
        errors.append("constitution: Node.js 22 must stay pinned for the web app")
    workflow = (root / ".github/workflows/ci.yml").read_text()
    for required in ("uv sync --locked", "npm ci"):
        if required not in workflow:
            errors.append(f"constitution: CI must run {required}")
    return errors


def spec_change_required(paths: list[str], messages: list[str], env: dict[str, str]) -> bool:
    if env.get("QTRADE_CONSTITUTION_SKIP_SPEC") == "1":
        return False
    if any("[no-spec-change]" in message for message in messages):
        return False
    behavior = any(path.startswith(("apps/", "packages/", "adapters/")) for path in paths)
    if not behavior:
        return False
    return not any(path.startswith("openspec/changes/") for path in paths)


def check_spec_diff(root: Path, base: str) -> list[str]:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git failed")
        return result.stdout

    for candidate in (base, f"origin/{base}"):
        try:
            git("rev-parse", "--verify", candidate)
        except RuntimeError:
            continue
        try:
            changed = git("diff", "--name-only", f"{candidate}...HEAD")
            paths = [line for line in changed.splitlines() if line]
            messages = git("log", "--format=%s", f"{candidate}..HEAD").splitlines()
        except RuntimeError as exc:
            return [f"constitution: cannot diff against {candidate}: {exc}"]
        if not spec_change_required(paths, messages, dict(os.environ)):
            return []
        return [
            "constitution: behavior change without an openspec/changes delta; "
            "add the delta or mark the commit [no-spec-change]"
        ]
    return [f"constitution: cannot resolve base ref {base!r}"]


def check(root: Path = ROOT) -> list[str]:
    text = (root / "CONSTITUTION.md").read_text()
    errors = check_document(text, root)
    errors.extend(check_dynamic_execution(root))
    errors.extend(check_password_auth(root))
    errors.extend(check_tracked_secrets(root))
    errors.extend(check_toolchain(root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--spec-diff",
        metavar="BASE",
        help="require an openspec change when the diff against BASE touches behavior",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    errors = check(root)
    if args.spec_diff:
        errors.extend(check_spec_diff(root, args.spec_diff))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Constitution checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
