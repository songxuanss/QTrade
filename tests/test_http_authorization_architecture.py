"""Fail when a new browser business route forgets the central security guards."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def violations(source: str) -> list[str]:
    tree = ast.parse(source)
    protected_routers = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        if isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "APIRouter"
        and any(
            k.arg == "prefix"
            and isinstance(k.value, ast.Constant)
            and str(k.value.value).startswith("/api/v1")
            for k in node.value.keywords
        )
    }
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        routes = [
            d
            for d in node.decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id in protected_routers
        ]
        for route in routes:
            method = route.func.attr
            if method == "websocket":
                guarded = any(
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "websocket_workspace"
                    for call in ast.walk(node)
                )
                if not guarded:
                    found.append(f"{node.name}: websocket requires central RBAC/CSRF guard")
                continue
            mutation = method not in {"get", "head", "options"}
            guards = [
                call.args[0]
                for argument in node.args.args
                if argument.annotation is not None
                for call in ast.walk(argument.annotation)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "Depends"
                and call.args
            ]
            actor = any(isinstance(g, ast.Attribute) and g.attr == "actor" for g in guards)
            workspace = [
                g
                for g in guards
                if isinstance(g, ast.Call)
                and isinstance(g.func, ast.Attribute)
                and g.func.attr == "workspace"
            ]
            if mutation:
                valid = any(
                    any(
                        k.arg == "mutate"
                        and isinstance(k.value, ast.Constant)
                        and k.value.value is True
                        for k in g.keywords
                    )
                    for g in workspace
                )
            else:
                valid = actor or bool(workspace)
            if not valid:
                found.append(
                    f"{node.name}: {method} requires central "
                    f"{'RBAC/CSRF' if mutation else 'authentication'} guard"
                )
    return found


def test_business_routes_always_declare_central_authentication_and_mutation_guards():
    errors = [
        f"{path.name}: {message}"
        for path in (ROOT / "apps/api/src/qtrade_api").glob("*.py")
        for message in violations(path.read_text())
    ]
    assert errors == []


def test_architecture_gate_rejects_unprotected_new_routes_and_missing_csrf():
    source = """
router = APIRouter(prefix="/api/v1/workspaces")
@router.get("/{workspace_id}")
def leak(): return {"private": True}
@router.post("/{workspace_id}")
def unguarded_write(
    access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))]
): pass
@router.websocket("/{workspace_id}/events")
async def unguarded_socket(websocket): pass
@router.websocket("/{workspace_id}/safe-events")
async def guarded_socket(websocket):
    security.websocket_workspace(websocket, workspace_id, Permission.READ)
"""
    assert len(violations(source)) == 3
