"""Trusted-consumer delivery inventory, with current actor authorization at ack."""

from typing import Protocol
from uuid import UUID

from qtrade_data_contracts.freshness_delivery import (
    FreshnessNoticeAck,
    FreshnessNoticeClaim,
    FreshnessNoticeLease,
)
from qtrade_data_core.ports import DataAuthorizer, DataFailure


class FreshnessDeliveryRepository(Protocol):
    def claim(self, request: FreshnessNoticeClaim) -> tuple[FreshnessNoticeLease, ...]: ...
    def acknowledge(
        self, actor: UUID, scope: UUID, notice_id: UUID, consumer: str, token: UUID
    ) -> bool: ...


class FreshnessDelivery:
    def __init__(
        self, auth: DataAuthorizer, repository: FreshnessDeliveryRepository, consumer: str
    ):
        self.auth, self.repository = auth, repository
        self.consumer = FreshnessNoticeClaim(consumer_id=consumer).consumer_id

    def claim(self, request: FreshnessNoticeClaim) -> tuple[FreshnessNoticeLease, ...]:
        """Service-token inventory only; the consuming application rechecks actors.

        The independent collector never imports the consumer's workspace model.
        It checks current manage permission again when accepting an ack.
        """
        if request.consumer_id != self.consumer:
            raise DataFailure("forbidden")
        leases = self.repository.claim(request)
        if len(leases) > request.limit:
            raise DataFailure("unavailable")
        for lease in leases:
            try:
                FreshnessNoticeLease.model_validate_json(lease.model_dump_json())
            except ValueError:
                raise DataFailure("unverified") from None
            if lease.consumer_id != request.consumer_id:
                raise DataFailure("unverified")
        return leases

    def acknowledge(
        self, actor: UUID, scope: UUID, notice_id: UUID, body: FreshnessNoticeAck
    ) -> None:
        self.auth.require(actor, scope, "manage")
        if body.consumer_id != self.consumer:
            raise DataFailure("forbidden")
        if not self.repository.acknowledge(actor, scope, notice_id, body.consumer_id, body.token):
            raise DataFailure("conflict")
