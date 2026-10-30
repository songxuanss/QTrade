from dataclasses import replace
from datetime import timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from qtrade_application.artifacts import ManifestService
from qtrade_contracts import ArtifactManifest, MarketDataset, StrategyVersion
from qtrade_domain.artifacts import ManifestInputs, build_manifest, manifest_bytes
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.ports import MockObjectStore

FIXTURES = Path(__file__).parent / "fixtures/contracts"


def load(model):
    return model.model_validate_json((FIXTURES / f"{model.__name__}.json").read_text())


@pytest.fixture
def inputs():
    reference = load(ArtifactManifest)
    return ManifestInputs(
        reference.run_id,
        reference.kind,
        reference.created_by,
        reference.created_at,
        load(StrategyVersion),
        load(MarketDataset),
        reference.parameters_json,
        reference.engine_name,
        reference.engine_version,
        reference.adapter_version,
        reference.environment_image_digest,
        reference.random_seed,
        execution_config_json=reference.execution_config_json,
    )


def test_manifest_stable_identity_canonical_parameters_and_time(inputs):
    first = build_manifest(replace(inputs, parameters_json='{"b":2,"a":1}'))
    equivalent = build_manifest(
        replace(
            inputs,
            parameters_json=' { "a": 1, "b": 2 } ',
            created_at=inputs.created_at.astimezone(timezone(timedelta(hours=8))),
        )
    )
    assert first == equivalent
    assert manifest_bytes(first) == manifest_bytes(equivalent)
    assert first.parameters_sha256 == sha256(b'{"a":1,"b":2}').hexdigest()
    assert build_manifest(inputs).manifest_id != first.manifest_id


@pytest.mark.parametrize(
    "change",
    [
        {"random_seed": 1},
        {"engine_version": "2"},
        {"adapter_version": "2"},
        {"environment_image_digest": "sha256:" + "b" * 64},
        {"created_by": uuid4()},
        {"run_id": uuid4()},
        {"nondeterministic_sources": ("clock",)},
        {"execution_config_json": '{"cost_model_version":"changed"}'},
    ],
)
def test_manifest_records_every_input_and_changes_identity(inputs, change):
    assert (
        build_manifest(inputs).manifest_id != build_manifest(replace(inputs, **change)).manifest_id
    )


def test_historical_manifest_cannot_be_replaced_by_new_inputs(inputs):
    service = ManifestService(MockObjectStore())
    manifest, ref = service.create(inputs)
    assert service.create(inputs) == (manifest, ref)
    revised = replace(
        inputs, dataset=inputs.dataset.model_copy(update={"content_sha256": "b" * 64})
    )
    with pytest.raises(PortError) as caught:
        service.create(revised)
    assert caught.value.code == ErrorCode.CONFLICT
    assert service.get(manifest.workspace_id, manifest.run_id) == manifest
    next_run = replace(revised, run_id=uuid4())
    next_manifest, _ = service.create(next_run)
    assert next_manifest.dataset_sha256 == "b" * 64
    assert (
        service.get(manifest.workspace_id, manifest.run_id).dataset_sha256
        == inputs.dataset.content_sha256
    )
    with pytest.raises(PortError) as caught:
        service.get(uuid4(), manifest.run_id)
    assert caught.value.code == ErrorCode.NOT_FOUND


@pytest.mark.parametrize(
    "change",
    [
        {"parameters_json": '{"duplicate":1,"duplicate":2}'},
        {"parameters_json": '{"x":NaN}'},
        {"parameters_json": "[]"},
        {"random_seed": -1},
        {"environment_image_digest": "latest"},
    ],
)
def test_invalid_manifest_inputs_rejected(inputs, change):
    with pytest.raises(PortError) as caught:
        build_manifest(replace(inputs, **change))
    assert caught.value.code == ErrorCode.INVALID_INPUT


def test_manifest_scope_and_naive_timestamp_rejected(inputs):
    for changed in [
        replace(inputs, dataset=inputs.dataset.model_copy(update={"workspace_id": uuid4()})),
        replace(inputs, created_at=inputs.created_at.replace(tzinfo=None)),
    ]:
        with pytest.raises(PortError) as caught:
            build_manifest(changed)
        assert caught.value.code == ErrorCode.INVALID_INPUT


def test_read_rejects_content_with_valid_hash_but_wrong_resource_identity(inputs):
    store = MockObjectStore()
    service = ManifestService(store)
    manifest = build_manifest(inputs)
    wrong_run = uuid4()
    content = manifest_bytes(manifest)
    store.put(
        manifest.workspace_id,
        f"runs/{wrong_run}/manifest.v1.json",
        content,
        content_type="application/json",
        expected_sha256=sha256(content).hexdigest(),
    )
    with pytest.raises(PortError) as caught:
        service.get(manifest.workspace_id, wrong_run)
    assert caught.value.code == ErrorCode.CHECKSUM_MISMATCH
