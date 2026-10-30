"""Compose fixed query templates with quoted column identifiers, never raw interpolation."""

from psycopg import sql


def column_query(template: str, names: str) -> sql.Composed:
    return sql.SQL(template).format(
        sql.SQL(",").join(sql.Identifier(name.strip()) for name in names.split(","))
    )
