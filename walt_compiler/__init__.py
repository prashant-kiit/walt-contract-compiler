"""Compile a query contract + semantic model into SQL. See DESIGN.md."""
from walt_compiler.contract import parse_contract
from walt_compiler.dialects import get_dialect
from walt_compiler.errors import CompilerError
from walt_compiler.lower import lower
from walt_compiler.model import build_catalog
from walt_compiler.options import CompileOptions
from walt_compiler.resolve import resolve

__version__ = "0.1.0"
__all__ = ["CompileOptions", "CompilerError", "compile_contract"]


def compile_contract(semantic_model: dict, contract: dict, dialect: str,
                     options: CompileOptions | None = None) -> str:
    """Compile a contract against a semantic model into a SQL string for `dialect`.

    Pure and deterministic: the same inputs always give byte-identical SQL. Raises a
    CompilerError subclass for anything unknown, unsupported or inconsistent.
    """
    options = options or CompileOptions()
    target = get_dialect(dialect, options)
    catalog = build_catalog(semantic_model, options)
    plan = resolve(parse_contract(contract, options), catalog, options, target.max_identifier_length, target.name)
    return target.render(lower(plan))
