"""Compatibility outcome semantics: calendar evidence is not a market observation."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from qtrade_composition.collection_gateway import ExistingCollectionExecutor
from qtrade_contracts import DataBatch
from qtrade_domain.batches import ArchivedBatch
from qtrade_domain.raw_records import RawRecord


def test_calendar_only_archive_is_no_observation_not_viewable():
    scope, task_id, batch_id = uuid4(), uuid4(), uuid4()
    metadata = DataBatch.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/DataBatch.json").read_text()
    ).model_copy(
        update={
            "workspace_id": scope,
            "batch_id": batch_id,
            "record_count": 1,
            "instrument_id": "601288.SH",
            "source": "mairui.a-share.bar",
            "granularity": "1d",
        }
    )
    archive = ArchivedBatch(metadata, (RawRecord(0, b'{"kind":"calendar","days":[]}'),))
    task = SimpleNamespace(
        status="completed",
        batch_ids=(batch_id,),
        dataset_id=uuid4(),
        request=SimpleNamespace(
            source="mairui.a-share.bar",
            granularity="1d",
            start=metadata.collected_at,
            end=metadata.collected_at,
        ),
    )

    def unpublished(*args):
        raise AssertionError("calendar-only response must not be classified from publication")

    executor = ExistingCollectionExecutor(
        SimpleNamespace(authorize=lambda *args: object()),
        None,
        SimpleNamespace(get=lambda *args: task),
        SimpleNamespace(read=lambda *args: archive),
        SimpleNamespace(get=unpublished),
        SimpleNamespace(query_bars=unpublished),
    )
    result = executor.result(SimpleNamespace(actor_id=uuid4(), scope_id=scope), task_id)
    assert result["state"] == "no_observation"
    assert result["raw_records"] == 1 and result["observed_records"] == 0
