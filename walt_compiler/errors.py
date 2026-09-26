"""Every failure the compiler reports is a named CompilerError (DESIGN.md §9).

An error carries a stable `code` (its class name), a JSON-pointer `path` into the contract or
semantic model, a human-readable `message`, and optional "did you mean" `suggestions`.
"""
from collections.abc import Iterable


class CompilerError(Exception):
    def __init__(self, message: str, path: str = "", suggestions: Iterable[str] = ()):
        self.message = message
        self.path = path
        self.suggestions = tuple(suggestions)
        super().__init__(str(self))

    @property
    def code(self) -> str:
        return type(self).__name__

    def __str__(self) -> str:
        where = f" at {self.path}" if self.path else ""
        text = f"{self.code}{where}: {self.message}"
        if self.suggestions:
            text += f" Did you mean: {', '.join(self.suggestions)}?"
        return text


# Invalid input documents
class InvalidSemanticModel(CompilerError): pass
class InvalidContract(CompilerError): pass

# Names that do not exist
class UnknownDialect(CompilerError): pass
class UnknownMetric(CompilerError): pass
class UnknownDimension(CompilerError): pass

# Valid requests the compiler deliberately does not support
class UnsupportedOperator(CompilerError): pass
class UnsupportedAggregation(CompilerError): pass
class UnsupportedFeature(CompilerError): pass
class UnsupportedRelationship(CompilerError): pass

# Requests that do not make sense against this model
class InvalidLiteral(CompilerError): pass
class ModelMismatch(CompilerError): pass
class DuplicateOutputName(CompilerError): pass
class NoJoinPath(CompilerError): pass
class AmbiguousJoinPath(CompilerError): pass
class MultipleFactTables(CompilerError): pass
class InvalidCompare(CompilerError): pass
class ConflictingFilter(CompilerError): pass

# Names the target dialect cannot represent
class IdentifierTooLong(CompilerError): pass
