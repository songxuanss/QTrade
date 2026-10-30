"""One incident spans repeated failures and unknown observations until recovery."""

from datetime import UTC
from typing import Literal
from uuid import UUID, uuid5

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.freshness import FreshnessResult
from qtrade_data_contracts.freshness_notice import FreshnessNotice
from qtrade_data_core.ports import DataFailure


def transition_notice(
    actor: UUID, result: FreshnessResult, previous: FreshnessNotice | None
) -> FreshnessNotice | None:
    """Decide from the last incident transition, not merely the previous check.

    An intervening unknown/grace result cannot close an incident or cause a
    duplicate opening on the next failure. Consumers receive two stable event
    identities per episode and may deliver them at least once after restart.
    """
    if previous is not None:
        if previous.result.key != result.key or previous.occurred_at >= result.checked_at:
            raise DataFailure("conflict")
    active = previous is not None and previous.kind == "opened"
    kind: Literal["opened", "recovered"]
    if result.status in {"late", "unavailable"} and not active:
        kind = "opened"
        opened_at = result.checked_at
        episode = uuid5(
            result.scope_id,
            f"freshness-episode:{digest(result.key.model_dump(mode='json'))}:"
            f"{opened_at.astimezone(UTC).isoformat()}",
        )
    elif (
        result.status == "current"
        and result.evidence.source_health == "available"
        and active
        and previous is not None
    ):
        kind, opened_at, episode = "recovered", previous.opened_at, previous.episode_id
    else:
        return None
    return FreshnessNotice(
        schema_version=1,
        scope_id=result.scope_id,
        actor_id=actor,
        notice_id=uuid5(episode, kind),
        episode_id=episode,
        kind=kind,
        opened_at=opened_at,
        occurred_at=result.checked_at,
        result=result,
    )
