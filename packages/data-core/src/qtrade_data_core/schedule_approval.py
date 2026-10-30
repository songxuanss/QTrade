"""A frozen automatic acknowledgement bounds each new submission, not spending.

Actual HTTP calls, metadata, retries and concurrent runs still use the shared
hard usage ledger. These caps bound the current no-retry planning estimate.
"""

from dataclasses import dataclass

from qtrade_data_contracts.collection import CollectionPreview
from qtrade_data_contracts.schedules import CollectionScheduleDraft, MarketScheduleSelection
from qtrade_data_core.ports import DataFailure


class ScheduleApprovalRequired(DataFailure):
    """The current plan exceeds frozen approval; a dispatcher must pause it."""

    def __init__(self) -> None:
        super().__init__("invalid_input")


@dataclass(frozen=True)
class ScheduleExecutionApproval:
    draft: CollectionScheduleDraft

    def check(self, preview: CollectionPreview) -> None:
        request, selection = preview.request, self.draft.selection
        if (
            self.draft.automation_confirmed is not True
            or not isinstance(selection, MarketScheduleSelection)
            or request.scope_id != self.draft.scope_id
            or request.actor_id != self.draft.actor_id
            or request.source != selection.source
            or request.dataset_kind != selection.dataset_kind
            or request.instrument_ids != selection.instrument_ids
            or request.granularity != selection.granularity
            or request.fields != selection.fields
            or request.mode != "missing"
        ):
            raise DataFailure("conflict")
        # Unknown estimation is not permission for unlimited execution. Check
        # the freshly rebuilt plan inside admission, never just an old preview.
        estimate = preview.execution_estimate
        if estimate is None:
            raise DataFailure("unverified")
        if (
            preview.missing_parts > self.draft.approved_parts
            or estimate.credits_max > self.draft.approved_credits
        ):
            raise ScheduleApprovalRequired()
