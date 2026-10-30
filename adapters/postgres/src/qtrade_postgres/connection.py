"""Shared transaction/schema/error boundary for identity persistence adapters."""

import re
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql

from qtrade_domain.ports import ErrorCode, PortError


class PostgresStore:
    def __init__(self, database_url: str, *, schema: str = "qtrade"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid database schema")
        self._url, self._schema = database_url, schema

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
        try:
            with psycopg.connect(self._url) as connection:
                connection.execute(
                    sql.SQL("SET search_path TO {}, pg_catalog").format(
                        sql.Identifier(self._schema)
                    )
                )
                yield connection
        except psycopg.errors.UniqueViolation:
            raise PortError(ErrorCode.CONFLICT, "record already exists") from None
        except psycopg.IntegrityError:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid persistence record") from None
        except psycopg.Error:
            raise PortError(ErrorCode.UNAVAILABLE, "identity persistence unavailable") from None
