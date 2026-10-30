"""Verify S3 semantics against local Compose MinIO, including concurrent creates.

Uses a fresh dedicated test bucket, removes only that bucket in finally. Local
credentials are read in memory from the Compose container and never printed.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
for directory in (
    "packages/contracts",
    "packages/domain",
    "adapters/object_store",
    "adapters/mock",
):
    sys.path.insert(0, str(ROOT / directory / "src"))

from qtrade_domain.ports import ErrorCode, ObjectStorePort, PortError  # noqa: E402
from qtrade_mock.ports import MockObjectStore  # noqa: E402
from qtrade_object_store.s3 import S3ObjectStore  # noqa: E402


def rejected(action, code):
    try:
        action()
    except PortError as error:
        assert error.code == code, f"expected {code}, got {error.code}"
    else:
        raise AssertionError(f"expected {code}")


def verify(store: ObjectStorePort):
    workspace = uuid4()
    payload = b"qtrade immutable artifact"
    digest = sha256(payload).hexdigest()
    ref = store.put(
        workspace,
        "reports/one.json",
        payload,
        content_type="application/json",
        expected_sha256=digest,
    )
    assert store.get(workspace, ref.key, expected_sha256=digest) == payload
    assert store.stat(workspace, ref.key) == ref
    assert (
        store.put(
            workspace, ref.key, payload, content_type=ref.content_type, expected_sha256=digest
        )
        == ref
    )
    rejected(lambda: store.get(uuid4(), ref.key, expected_sha256=digest), ErrorCode.NOT_FOUND)
    rejected(lambda: store.get(workspace, "missing", expected_sha256=digest), ErrorCode.NOT_FOUND)
    rejected(
        lambda: store.get(workspace, ref.key, expected_sha256="0" * 64), ErrorCode.CHECKSUM_MISMATCH
    )
    rejected(
        lambda: store.put(
            workspace,
            ref.key,
            b"changed",
            content_type=ref.content_type,
            expected_sha256=sha256(b"changed").hexdigest(),
        ),
        ErrorCode.CONFLICT,
    )
    rejected(
        lambda: store.put(
            workspace, "new", payload, content_type=ref.content_type, expected_sha256="0" * 64
        ),
        ErrorCode.CHECKSUM_MISMATCH,
    )
    assert store.get(workspace, ref.key, expected_sha256=digest) == payload


def main():
    container = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "object-store"], cwd=ROOT, text=True
    ).strip()
    if not container:
        raise RuntimeError("start the local Compose object-store first")
    state = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    environment = dict(item.split("=", 1) for item in state["Config"]["Env"])
    address = next(iter(state["NetworkSettings"]["Networks"].values()))["IPAddress"]
    bucket = f"qtrade-selftest-{uuid4().hex}"
    store = S3ObjectStore.connect(
        bucket=bucket,
        endpoint_url=f"http://{address}:9000",
        allow_http=True,
        access_key=environment["MINIO_ROOT_USER"],
        secret_key=environment["MINIO_ROOT_PASSWORD"],
    )
    client = store._client  # self-test provisioning only; application uses ObjectStorePort.
    client.create_bucket(Bucket=bucket)
    try:
        verify(MockObjectStore())
        verify(store)
        workspace = uuid4()

        def race(value):
            try:
                ref = store.put(
                    workspace,
                    "race",
                    value,
                    content_type="text/plain",
                    expected_sha256=sha256(value).hexdigest(),
                )
                return ref.content_sha256
            except PortError as error:
                assert error.code == ErrorCode.CONFLICT
                return None

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(race, [f"candidate-{i}".encode() for i in range(8)]))
        assert len([result for result in results if result]) == 1
        winner = store.stat(workspace, "race")
        assert winner.content_sha256 in results
        store.get(workspace, "race", expected_sha256=winner.content_sha256)
        # Corruption through a privileged SDK write must be caught by the Port.
        client.put_object(
            Bucket=bucket,
            Key=f"workspaces/{workspace}/corrupt",
            Body=b"evil",
            Metadata={"sha256": sha256(b"good").hexdigest()},
        )
        rejected(
            lambda: store.get(workspace, "corrupt", expected_sha256=sha256(b"good").hexdigest()),
            ErrorCode.CHECKSUM_MISMATCH,
        )
        print(
            "Mock/S3 contract, MinIO persistence, isolation, corruption and concurrent create: PASS"
        )
    finally:
        objects = client.list_objects_v2(Bucket=bucket).get("Contents", [])
        for item in objects:
            client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)


if __name__ == "__main__":
    main()
