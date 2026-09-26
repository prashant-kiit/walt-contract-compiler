from walt_compiler.dialects.base import Dialect


class PostgresDialect(Dialect):
    """Postgres spells everything this compiler emits in standard SQL (FILTER since 9.4, GROUPING SETS
    since 9.5), but truncates identifiers longer than NAMEDATALEN - 1 = 63 bytes."""
    name = "postgres"
    max_identifier_length = 63
