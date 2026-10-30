"""QTrade-specific authorization bridge; the portable catalog never imports this module."""

import os
from collections.abc import Mapping
from uuid import UUID

from qtrade_application.workspaces import WorkspaceService
from qtrade_data_core.ports import Action, DataFailure
from qtrade_data_core.source_profiles import SourceProfileDirectory
from qtrade_data_core.universes import UniverseCatalog
from qtrade_data_mairui.reference import MaiRuiReference
from qtrade_data_mairui.source_profiles import reference_profile
from qtrade_data_postgres.universes import PostgresUniverses
from qtrade_domain.authorization import Permission
from qtrade_domain.ports import ErrorCode, PortError


class QTradeDataAuthorizer:
    def __init__(self, workspaces: WorkspaceService):
        self._workspaces = workspaces

    def require(self, actor_id: UUID, scope_id: UUID, action: Action) -> None:
        try:
            self._workspaces.authorize(
                actor_id,
                scope_id,
                Permission.DATA_MANAGE if action == "manage" else Permission.READ,
            )
        except PortError as error:
            if error.code == ErrorCode.NOT_FOUND:
                raise DataFailure("not_found") from None
            raise DataFailure("forbidden") from None


def universe_catalog_from_env(
    workspaces: WorkspaceService | None,
    env: Mapping[str, str] = os.environ,
) -> UniverseCatalog | None:
    if workspaces is None or not env.get("MAIRUI_LICENCE"):
        return None
    return UniverseCatalog(
        QTradeDataAuthorizer(workspaces),
        MaiRuiReference(env["MAIRUI_LICENCE"]),
        PostgresUniverses(env.get("DATA_COLLECTION_DATABASE_URL", env["DATABASE_URL"])),
    )


def universe_source_profiles_from_env(
    workspaces: WorkspaceService | None,
    env: Mapping[str, str] = os.environ,
) -> SourceProfileDirectory | None:
    # This deployment owns reference resolution; collector configuration is
    # intentionally not used as evidence that this adapter is configured.
    if workspaces is None:
        return None
    return SourceProfileDirectory(
        QTradeDataAuthorizer(workspaces), (reference_profile(bool(env.get("MAIRUI_LICENCE"))),)
    )
