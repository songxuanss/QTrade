"""One leased research run; source execution and publication retain separate gates."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_data_contracts.research_transport import ResearchPlan
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchProvider
from qtrade_data_core.research_collection import ResearchCollectionExecutor
from qtrade_data_core.research_release import ResearchReleaseService
from qtrade_data_core.research_replay import ResearchReplayService
from qtrade_data_core.usage import DenialReason, UsageDenied


@dataclass(frozen=True)
class ResearchJobLease:
    plan: ResearchPlan
    lease_id: UUID
    attempt: int


class ResearchQueue(Protocol):
    def claim(self) -> ResearchJobLease | None: ...
    def finish(
        self, lease: ResearchJobLease, *, publication: UUID | None = None, error: str | None = None
    ) -> None: ...
    def wait_budget(
        self, lease: ResearchJobLease, reason: DenialReason, retry_at: datetime | None
    ) -> None: ...


class ResearchWorker:
    def __init__(
        self,
        queue: ResearchQueue,
        executor: ResearchCollectionExecutor,
        release: ResearchReleaseService,
        providers: tuple[ResearchProvider, ...],
        *,
        replays: ResearchReplayService | None = None,
    ):
        self.queue, self.executor, self.release, self.providers = (
            queue,
            executor,
            release,
            providers,
        )
        self.replays = replays

    def run_once(self) -> bool:
        lease = self.queue.claim()
        if lease is None:
            return False
        req = lease.plan.request
        try:
            matches = [p for p in self.providers if lease.plan.capability in p.capabilities()]
            if len(matches) != 1:
                raise DataFailure("unsupported")
            if lease.plan.plan_version == "research-replay-plan.v1":
                # An offline intent must never fall back to network collection,
                # including a missing/corrupt lineage record or disabled wiring.
                if self.replays is None:
                    raise DataFailure("unsupported")
                replay = self.replays.repository.replay(req.scope_id, req.request_id)
                if replay is None:
                    raise DataFailure("unverified")
                result = self.replays.execute(lease.plan, replay)
            else:
                result = self.executor.collect(lease.plan, matches[0])
            published = (
                self.release.publish(req.actor_id, req.scope_id, result.artifact.artifact_id)
                if result.artifact is not None
                else None
            )
        except UsageDenied as error:
            # Admission cannot consume a supplier failure allowance. The same
            # frozen run resumes after policy-derived eligibility, with auth
            # rechecked by the executor and immutable evidence retained.
            self.queue.wait_budget(lease, error.reason, error.retry_at)
        except DataFailure as error:
            self.queue.finish(lease, error=error.code)
        except Exception:
            # A programming/storage failure is not a supplier rejection. Close
            # the current lease with a safe recoverable code so the UI can offer
            # retry immediately, rather than leaving it running until expiry.
            # Do not copy exception text, which may contain provider secrets.
            self.queue.finish(lease, error="unavailable")
        else:
            self.queue.finish(lease, publication=published.publication_id if published else None)
        return True
