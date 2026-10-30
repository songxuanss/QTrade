"""Numeric archival must not turn a documented zero flag into a bad candle.

Legacy mappings remain reproducible for existing immutable references. New
source semantics need a registered mapping, not a rewrite of old releases.
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_normalization_inputs import row, setup

from qtrade_data_core.ports import DataFailure
from qtrade_data_mairui.normalization import normalize


def prepared(tmp_path, flag):
    provider, task, archive, *_ = setup(tmp_path, rows=[row("2025-09-01 00:00:00") | {"sf": flag}])
    request = task.request.model_copy(
        update={
            "source": "mairui.history.bars",
            "granularity": "1d",
            "start": datetime(2025, 8, 31, 16, tzinfo=UTC),
            "end": datetime(2025, 9, 1, 16, tzinfo=UTC),
        }
    )
    task = replace(
        task,
        request=request,
        start=request.start,
        end=request.end,
        mapping_version="mairui.history-bars.v3",
    )
    captures = []

    def capture(kind, payload):
        captures.append(archive.capture_raw(task, kind, payload))

    def save(payload):
        archive.capture_prepared(task, payload, tuple(captures))

    result = provider.fetch_prepared(task, capture=capture, prepared=save)
    return task, archive.normalization_inputs(task)[0], result


@pytest.mark.parametrize("flag", [0, 0.0, "0", "0.0", "0.000", "0E-6"])
def test_zero_source_flag_is_valid_after_exact_archival(tmp_path, flag):
    task, value, result = prepared(tmp_path, flag)
    assert len(result.records) == 1 and result.records[0].quality == "valid"
    assert normalize(task, value.payload) == result
    assert result.evidence["source_flag_unknown_count"] == 0


def test_legacy_mapping_remains_frozen_for_archived_decimal_zero(tmp_path):
    task, value, _ = prepared(tmp_path, 0.0)
    assert value.payload.slices[0].rows[0]["sf"] == "0.0"
    legacy = normalize(replace(task, mapping_version="mairui.history-bars.v2"), value.payload)
    assert legacy.records[0].quality == "suspect"


@pytest.mark.parametrize("flag", [1, "1.0", -1, "0.5", None])
def test_nonzero_or_unknown_source_flag_cannot_certify_normal_trading(tmp_path, flag):
    _, _, result = prepared(tmp_path, flag)
    assert result.records[0].quality == "suspect"
    assert result.evidence["source_flag_unknown_count"] == int(flag is None)


@pytest.mark.parametrize("flag", [True, "invalid", "NaN", "Infinity"])
def test_invalid_source_flag_is_rejected_after_input_is_saved(tmp_path, flag):
    with pytest.raises(DataFailure, match="unverified"):
        prepared(tmp_path, flag)
    assert len(list(tmp_path.rglob("input-*.json"))) == 1


def test_unchanged_price_volume_and_precision(tmp_path):
    _, _, result = prepared(tmp_path, "0.0")
    record = result.records[0]
    assert record.close == Decimal(10) and record.volume == Decimal(200)


def test_only_registered_ordinary_history_mapper_is_upgraded(tmp_path):
    provider, *_ = setup(tmp_path)
    catalog = {item.source: item.mapping_version for item in provider.datasets()}
    assert catalog["mairui.history.bars"] == "mairui.history-bars.v3"
    assert catalog["mairui.pro.bars"] == "mairui.pro-bars.v1"


def test_new_ordinary_flag_semantics_cannot_be_applied_to_pro_endpoint(tmp_path):
    task, value, _ = prepared(tmp_path, "0.0")
    task = replace(task, request=task.request.model_copy(update={"source": "mairui.pro.bars"}))
    with pytest.raises(DataFailure, match="conflict"):
        normalize(task, value.payload)


@pytest.mark.parametrize("with_extra_flag", [False, True])
def test_ordinary_index_does_not_require_stock_suspension_flag(tmp_path, with_extra_flag):
    task, value, _ = prepared(tmp_path, "0.0")
    instrument = "index:000001.SH"
    task = replace(
        task,
        instrument_id=instrument,
        request=task.request.model_copy(update={"instrument_ids": (instrument,)}),
    )
    slices = tuple(
        s.model_copy(
            update={
                "rows": tuple(
                    dict(row) if with_extra_flag else {k: v for k, v in row.items() if k != "sf"}
                    for row in s.rows
                )
            }
        )
        for s in value.payload.slices
    )
    result = normalize(task, value.payload.model_copy(update={"slices": slices}))
    assert result.records[0].quality == "valid"
    assert result.evidence["source_flag_semantics"] == "not_applicable_for_index"
    assert result.evidence["source_flag_unknown_count"] == 0
