"""Caller-tunable options. They only affect error reporting, never the emitted SQL."""
from dataclasses import dataclass

from walt_compiler.suggest import DEFAULT_LIMIT, DEFAULT_THRESHOLD


@dataclass(frozen=True)
class CompileOptions:
    suggestion_threshold: float = DEFAULT_THRESHOLD
    max_suggestions: int = DEFAULT_LIMIT

    def __post_init__(self):
        if not 0.0 <= self.suggestion_threshold <= 1.0:
            raise ValueError("suggestion_threshold must be between 0 and 1")
        if self.max_suggestions < 0:
            raise ValueError("max_suggestions must be >= 0")
