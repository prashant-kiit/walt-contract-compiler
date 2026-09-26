"""Dialect registry. A new dialect is a Dialect subclass registered here (DESIGN.md §8)."""
from types import MappingProxyType

from walt_compiler.dialects.base import Dialect
from walt_compiler.dialects.duckdb import DuckDBDialect
from walt_compiler.errors import UnknownDialect
from walt_compiler.options import CompileOptions
from walt_compiler.suggest import suggest

DIALECTS = MappingProxyType({d.name: d for d in (DuckDBDialect(),)})


def get_dialect(name: str, options: CompileOptions = CompileOptions()) -> Dialect:
    dialect = DIALECTS.get(name)
    if dialect is None:
        raise UnknownDialect(f"{name!r} is not a supported dialect (supported: {', '.join(DIALECTS)}).",
                             suggestions=suggest(name, DIALECTS, options.suggestion_threshold,
                                                 options.max_suggestions))
    return dialect
