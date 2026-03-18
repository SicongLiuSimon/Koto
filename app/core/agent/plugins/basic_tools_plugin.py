import ast
from datetime import datetime
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin


class BasicToolsPlugin(AgentPlugin):
    @property
    def name(self) -> str:
        return "BasicTools"

    @property
    def description(self) -> str:
        return "Basic utility tools for testing and general assistance."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_current_time",
                "func": self.get_current_time,
                "description": "Returns the current date and time.",
            },
            {
                "name": "calculate",
                "func": self.calculate,
                "description": "Performs basic arithmetic calculation.",
                # Explicit parameters example
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "expression": {
                            "type": "STRING",
                            "description": "Mathematical expression to evaluate (e.g. '2 + 2')",
                        }
                    },
                    "required": ["expression"],
                },
            },
        ]

    def get_current_time(self) -> str:
        """Returns the current local date and time."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # AST nodes allowed in arithmetic expressions
    _CALC_ALLOWED_NODES = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Constant,
        ast.Num,
        ast.Call,
        ast.Name,
        ast.Load,
    )
    _CALC_ALLOWED_NAMES = frozenset(
        {"abs", "round", "min", "max", "sum", "int", "float"}
    )

    def calculate(self, expression: str) -> str:
        """Evaluates a simple mathematical expression."""
        try:
            tree = ast.parse(expression, mode="eval")
            for node in ast.walk(tree):
                if not isinstance(node, self._CALC_ALLOWED_NODES):
                    return f"Error: Disallowed syntax node: {type(node).__name__}"
                if (
                    isinstance(node, ast.Name)
                    and node.id not in self._CALC_ALLOWED_NAMES
                ):
                    return f"Error: Name '{node.id}' is not allowed. Only basic math functions are permitted."
            result = eval(expression, {"__builtins__": None}, {})  # nosec B307
            return str(result)
        except Exception as e:
            return f"Error calculating '{expression}': {str(e)}"
