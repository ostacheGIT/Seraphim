"""Operator system — named, persistent agent configurations with optional scheduling."""

from seraphim.operators.manifest import OperatorManifest
from seraphim.operators.manager import OperatorManager, get_operator_manager

__all__ = ["OperatorManifest", "OperatorManager", "get_operator_manager"]
