"""Static policy checks for strategy source before a runner is allocated.

This module provides fast feedback and a lifecycle gate. It is deliberately not
an execution sandbox: untrusted source still has to run in the isolated runner.
"""

import ast
import builtins
import re
import sys
from dataclasses import dataclass

MAX_SOURCE_BYTES = 1_000_000
MAX_AST_NODES = 20_000
MAX_NESTED_LOOPS = 3
MAX_RANGE_ITERATIONS = 1_000_000
MAX_DEPENDENCIES = 128

_BLOCKED_MODULES = {
    "builtins",
    "ctypes",
    "importlib",
    "io",
    "multiprocessing",
    "os",
    "pathlib",
    "pickle",
    "resource",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
    "threading",
    "urllib",
    "requests",
    "httpx",
    "ftplib",
    "telnetlib",
    "http",
    "urllib3",
    "aiohttp",
    "websocket",
    "websockets",
    "sqlite3",
    "shelve",
    "dbm",
    "mmap",
    "zipfile",
    "tarfile",
}
_BLOCKED_CALLS = {"__import__", "compile", "eval", "exec", "input", "open", "breakpoint"}
_SAFE_STDLIB = set(sys.stdlib_module_names) | set(vars(builtins))
_SAFE_STDLIB.add("qtrade")
_DEPENDENCY_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
_PINNED_VERSION = re.compile(r"^==[A-Za-z0-9][A-Za-z0-9.+!_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class StrategyDiagnostic:
    code: str
    severity: str
    message: str
    line: int | None = None
    column: int | None = None


@dataclass(frozen=True, slots=True)
class StrategyValidation:
    diagnostics: tuple[StrategyDiagnostic, ...]
    ast_node_count: int

    @property
    def passed(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


def validate_strategy_source(code: str, dependencies: dict[str, str]) -> StrategyValidation:
    """Check syntax, declared imports, unsafe capabilities and known lookahead forms."""
    diagnostics: list[StrategyDiagnostic] = []
    if not isinstance(code, str) or not code.strip():
        return StrategyValidation(
            (StrategyDiagnostic("source.empty", "error", "策略源码不能为空。"),), 0
        )
    source_size = len(code.encode("utf-8"))
    if source_size > MAX_SOURCE_BYTES:
        diagnostics.append(
            StrategyDiagnostic("resource.source_size", "error", "策略源码超过 1 MB 上限。")
        )
        return StrategyValidation(tuple(diagnostics), 0)
    if not isinstance(dependencies, dict) or len(dependencies) > MAX_DEPENDENCIES:
        return StrategyValidation(
            (StrategyDiagnostic("dependency.manifest", "error", "依赖清单格式或项目数量无效。"),),
            0,
        )

    normalized_dependencies: set[str] = set()
    for name, version in dependencies.items():
        if (
            not isinstance(name, str)
            or not isinstance(version, str)
            or not _DEPENDENCY_NAME.fullmatch(name)
            or not _PINNED_VERSION.fullmatch(version)
        ):
            diagnostics.append(
                StrategyDiagnostic(
                    "dependency.unpinned", "error", "每项第三方依赖都必须使用名称和精确版本（==）。"
                )
            )
            continue
        normalized_dependencies.add(re.sub(r"[-_.]+", "-", name).lower())

    try:
        tree = ast.parse(code, mode="exec", type_comments=True)
    except (SyntaxError, ValueError, RecursionError) as error:
        syntax_message = error.msg if isinstance(error, SyntaxError) else "源码结构无效"
        return StrategyValidation(
            tuple(
                diagnostics
                + [
                    StrategyDiagnostic(
                        "syntax.invalid",
                        "error",
                        f"Python 语法错误：{syntax_message}。",
                        getattr(error, "lineno", None),
                        getattr(error, "offset", None),
                    )
                ]
            ),
            0,
        )

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        diagnostics.append(
            StrategyDiagnostic("resource.ast_size", "error", "策略结构超过静态分析复杂度上限。")
        )
        return StrategyValidation(tuple(diagnostics), len(nodes))

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_import(alias.name, node, normalized_dependencies, diagnostics)
        elif isinstance(node, ast.ImportFrom) and node.module:
            _check_import(node.module, node, normalized_dependencies, diagnostics)
        elif isinstance(node, ast.Call):
            name = _qualified_name(node.func)
            leaf = name.rsplit(".", 1)[-1]
            if leaf in _BLOCKED_CALLS:
                diagnostics.append(
                    _diagnostic(
                        "capability.forbidden_call",
                        node,
                        f"禁止调用 {leaf}，策略不得访问宿主能力。",
                    )
                )
            if leaf in {"getattr", "setattr", "delattr", "globals", "locals", "vars"}:
                diagnostics.append(
                    _diagnostic(
                        "capability.dynamic_access", node, "禁止动态检查或修改 Python 运行时对象。"
                    )
                )
            if leaf in {
                "read_text",
                "read_bytes",
                "write_text",
                "write_bytes",
                "mkdir",
                "unlink",
                "rmdir",
                "read_csv",
                "read_parquet",
                "read_json",
                "read_excel",
                "read_pickle",
                "to_csv",
                "to_parquet",
                "to_json",
                "to_excel",
                "to_pickle",
            }:
                diagnostics.append(
                    _diagnostic("capability.filesystem", node, "禁止读写本地文件系统。")
                )
            if leaf in {
                "urlopen",
                "urlretrieve",
                "get",
                "post",
                "put",
                "delete",
                "request",
                "connect",
                "create_connection",
            } and any(
                part in name.lower()
                for part in ("requests", "httpx", "urllib", "socket", "urlopen")
            ):
                diagnostics.append(
                    _diagnostic("capability.network", node, "禁止访问外网或建立网络连接。")
                )
            if leaf == "shift" and node.args and _negative_integer(node.args[0]):
                diagnostics.append(
                    _diagnostic(
                        "lookahead.negative_shift", node, "检测到负向 shift，可能读取未来数据。"
                    )
                )
        elif isinstance(node, ast.While):
            diagnostics.append(
                _diagnostic("resource.unbounded_loop", node, "禁止使用无法静态界定的 while 循环。")
            )
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.iter, ast.Call) and _qualified_name(node.iter.func) == "range":
                bound = _range_bound(node.iter)
                if bound is None or bound > MAX_RANGE_ITERATIONS:
                    diagnostics.append(
                        _diagnostic(
                            "resource.loop_bound",
                            node,
                            "range 循环必须使用不超过 1,000,000 次的静态边界。",
                        )
                    )
        elif isinstance(node, ast.Name) and node.id == "__builtins__":
            diagnostics.append(
                _diagnostic("capability.dynamic_access", node, "禁止直接访问 Python 内建能力表。")
            )
        elif isinstance(node, ast.Subscript) and _positive_integer(node.slice):
            target = _qualified_name(node.value).lower().split(".")[-1]
            if target in {
                "bar",
                "bars",
                "data",
                "history",
                "prices",
                "close",
                "high",
                "low",
                "open",
                "volume",
            }:
                diagnostics.append(
                    _diagnostic(
                        "lookahead.positive_index",
                        node,
                        "正向序列索引可能读取未来数据；请从最新值使用负向索引。",
                    )
                )

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if _is_recursive(node):
                diagnostics.append(
                    _diagnostic("resource.recursion", node, "策略函数不得递归调用自身。")
                )

    _check_loop_depth(tree, diagnostics)
    return StrategyValidation(tuple(_deduplicate(diagnostics)), len(nodes))


def _check_import(
    module: str,
    node: ast.AST,
    dependencies: set[str],
    diagnostics: list[StrategyDiagnostic],
) -> None:
    root = module.split(".", 1)[0]
    normalized = re.sub(r"[-_.]+", "-", root).lower()
    if root in _BLOCKED_MODULES:
        category = (
            "network"
            if root
            in {
                "socket",
                "urllib",
                "requests",
                "httpx",
                "ftplib",
                "telnetlib",
                "http",
                "urllib3",
                "aiohttp",
                "websocket",
                "websockets",
            }
            else "filesystem"
        )
        diagnostics.append(
            _diagnostic(
                f"capability.{category}",
                node,
                "禁止网络访问。" if category == "network" else "禁止访问宿主文件系统与进程能力。",
            )
        )
    elif root not in _SAFE_STDLIB and normalized not in dependencies:
        diagnostics.append(
            _diagnostic(
                "dependency.missing",
                node,
                f"第三方模块 {root} 未在依赖清单中声明并锁定版本。",
            )
        )


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _qualified_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _negative_integer(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) is int
        and node.operand.value > 0
    )


def _positive_integer(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value > 0


def _range_bound(call: ast.Call) -> int | None:
    if not 1 <= len(call.args) <= 3 or call.keywords:
        return None
    values = []
    for arg in call.args:
        if not isinstance(arg, ast.Constant) or type(arg.value) is not int:
            return None
        values.append(arg.value)
    start, stop, step = (
        (0, values[0], 1)
        if len(values) == 1
        else ((values[0], values[1], 1) if len(values) == 2 else tuple(values))
    )
    if step == 0:
        return None
    try:
        return len(range(start, stop, step))
    except OverflowError:
        return None


def _is_recursive(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == function.name
        for node in ast.walk(function)
    )


def _check_loop_depth(tree: ast.AST, diagnostics: list[StrategyDiagnostic]) -> None:
    def visit(node: ast.AST, depth: int) -> None:
        next_depth = depth + 1 if isinstance(node, (ast.For, ast.AsyncFor, ast.While)) else depth
        if next_depth > MAX_NESTED_LOOPS:
            diagnostics.append(_diagnostic("resource.loop_depth", node, "循环嵌套最多允许 3 层。"))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, next_depth)

    visit(tree, 0)


def _diagnostic(code: str, node: ast.AST, message: str) -> StrategyDiagnostic:
    return StrategyDiagnostic(
        code,
        "error",
        message,
        getattr(node, "lineno", None),
        getattr(node, "col_offset", None),
    )


def _deduplicate(items: list[StrategyDiagnostic]) -> list[StrategyDiagnostic]:
    seen: set[tuple[str, int | None, int | None]] = set()
    result = []
    for item in items:
        key = (item.code, item.line, item.column)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
