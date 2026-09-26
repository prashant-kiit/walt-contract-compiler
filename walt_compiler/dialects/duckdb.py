from walt_compiler.dialects.base import Dialect


class DuckDBDialect(Dialect):
    """DuckDB spells everything this compiler emits in standard SQL."""
    name = "duckdb"
