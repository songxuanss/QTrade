"""Instrument master operations require explicit workspace authorization."""

from qtrade_contracts import Instrument
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.instruments import (
    InstrumentImport,
    InstrumentPage,
    InstrumentQuery,
    InstrumentStorePort,
    SpecificationConflict,
    validate_import,
)
from qtrade_domain.ports import ErrorCode, PortError


class InstrumentService:
    def __init__(self, store: InstrumentStorePort):
        self._store = store

    def import_instruments(
        self,
        access: WorkspaceAccess,
        instruments: tuple[Instrument, ...],
    ) -> tuple[InstrumentImport, ...]:
        access.require(Permission.DATA_MANAGE)
        validate_import(access.workspace.workspace_id, instruments)
        return self._store.import_instruments(
            access.user_id, access.workspace.workspace_id, instruments
        )

    def query(self, access: WorkspaceAccess, query: InstrumentQuery) -> InstrumentPage:
        access.require(Permission.READ, object_workspace_id=query.workspace_id)
        page = self._store.query(query)
        if any(item.workspace_id != query.workspace_id for item in page.instruments):
            raise PortError(ErrorCode.UNAVAILABLE, "instrument adapter returned invalid scope")
        return page

    def conflicts(self, access: WorkspaceAccess) -> tuple[SpecificationConflict, ...]:
        access.require(Permission.READ)
        issues = self._store.conflicts(access.workspace.workspace_id)
        if any(issue.workspace_id != access.workspace.workspace_id for issue in issues):
            raise PortError(ErrorCode.UNAVAILABLE, "instrument adapter returned invalid scope")
        return issues
