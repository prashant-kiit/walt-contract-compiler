"""Compile a query contract + semantic model into SQL. See DESIGN.md."""
from walt_compiler.errors import CompilerError
from walt_compiler.options import CompileOptions

__version__ = "0.1.0"
__all__ = ["CompileOptions", "CompilerError"]
