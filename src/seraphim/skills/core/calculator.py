import ast
import math

from seraphim.skills.base import BaseSkill, SkillResult

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
    ast.USub, ast.UAdd,
    ast.Call, ast.Name, ast.Load,
)

_SAFE_NAMES = {
    "sqrt": math.sqrt, "abs": abs, "round": round,
    "min": min, "max": max, "pow": pow,
    "log": math.log, "log2": math.log2, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "floor": math.floor, "ceil": math.ceil,
    "pi": math.pi, "e": math.e, "tau": math.tau,
}


class CalculatorSkill(BaseSkill):
    name = "calculator"
    description = (
        "Evaluate a math expression safely using AST. "
        "Supports +, -, *, /, **, %, sqrt, log, sin, cos, pi, abs, round, min, max."
    )
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate, e.g. '2**10 + sqrt(144)'",
            }
        },
        "required": ["expression"],
    }

    async def run(self, expression: str, **kwargs) -> SkillResult:
        try:
            tree = ast.parse(expression.strip(), mode="eval")
            for node in ast.walk(tree):
                if not isinstance(node, _ALLOWED_NODES):
                    return SkillResult(
                        success=False, output="",
                        error=f"Unsafe operation: {type(node).__name__}",
                    )
            result = eval(
                compile(tree, "<calculator>", "eval"),
                {"__builtins__": {}},
                _SAFE_NAMES,
            )
            formatted = int(result) if isinstance(result, float) and result == int(result) else result
            return SkillResult(success=True, output=f"{expression.strip()} = {formatted}")
        except ZeroDivisionError:
            return SkillResult(success=False, output="", error="Division by zero")
        except Exception as e:
            return SkillResult(success=False, output="", error=str(e))
