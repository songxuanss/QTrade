"""Portable discovery of dated evidence, independent from reference resolution."""

from uuid import UUID

from qtrade_data_contracts.source_profiles import SourceProfile, SourceProfileCatalog

from .ports import DataAuthorizer, DataFailure


class SourceProfileDirectory:
    def __init__(self, auth: DataAuthorizer, profiles: tuple[SourceProfile, ...]):
        self.auth, self.profiles = auth, profiles

    def read(self, actor: UUID, scope: UUID) -> SourceProfileCatalog:
        """Reauthorize every read; never resolve members or probe the supplier.

        Keeping evidence outside UniverseContent preserves immutable membership
        hashes when documentation is corrected or the deployment is moved.
        """
        self.auth.require(actor, scope, "read")
        try:
            return SourceProfileCatalog.model_validate_json(
                SourceProfileCatalog(
                    schema_version=1, scope_id=scope, profiles=self.profiles
                ).model_dump_json()
            )
        except ValueError:
            raise DataFailure("unverified") from None
