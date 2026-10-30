"""Validate the supported rule language before saving an unusable rule."""

import ast

from qtrade_domain.ports import ErrorCode, PortError

_ALLOWED = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
)


def validate_condition(expression: str) -> None:
    try:
        if not isinstance(expression, str) or not 1 <= len(expression) <= 512:
            raise ValueError
        tree = ast.parse(expression, mode="eval")
        nodes = tuple(ast.walk(tree))
        if len(nodes) > 64 or any(not isinstance(node, _ALLOWED) for node in nodes):
            raise ValueError
        if any(isinstance(node, ast.Name) and node.id.startswith("_") for node in nodes):
            raise ValueError
        if any(
            isinstance(node, ast.Constant) and type(node.value) not in {str, int, float, bool}
            for node in nodes
        ):
            raise ValueError
        # Only expressions with an explicit boolean result may become rules.
        if (
            not isinstance(tree.body, ast.Compare | ast.BoolOp)
            and not (isinstance(tree.body, ast.UnaryOp) and isinstance(tree.body.op, ast.Not))
            and not (isinstance(tree.body, ast.Constant) and type(tree.body.value) is bool)
        ):
            raise ValueError
    except (SyntaxError, TypeError, ValueError, RecursionError):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid alert condition") from None
