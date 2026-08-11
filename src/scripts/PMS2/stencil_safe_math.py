"""AST-walked arithmetic eval. Only +, -, *, / allowed.

Used by stencil_topo.py dry-run and merge_compute.py Phase 2.
No eval(). No builtins. No exponentiation.
"""

import ast
import operator

SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _safe_math_eval(expr: str) -> float:
    """Evaluate arithmetic expression. Only +, -, *, / allowed.

    Wraps SyntaxError -> ValueError so callers catch one type.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Unparseable expression: {expr}") from e
    return _eval_node(tree.body)


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in SAFE_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return SAFE_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_node(node.operand)
    raise ValueError(f"Unsafe expression node: {ast.dump(node)}")
