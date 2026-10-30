import base64
import io
from hashlib import sha256
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from qtrade_domain.ports import ErrorCode, PortError
from qtrade_object_store.s3 import S3ObjectStore, translated_error


@pytest.fixture
def setup():
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        endpoint_url="https://objects.invalid",
    )
    with Stubber(client) as stub:
        yield S3ObjectStore(client, "test-bucket"), stub, uuid4()
        stub.assert_no_pending_responses()


def params(workspace, key="report"):
    return {"Bucket": "test-bucket", "Key": f"workspaces/{workspace}/{key}"}


def test_conditional_upload_and_idempotent_retry(setup):
    store, stub, workspace = setup
    content = b"report"
    digest = sha256(content).hexdigest()
    upload = {
        **params(workspace),
        "Body": content,
        "ContentType": "application/json",
        "Metadata": {"sha256": digest},
        "IfNoneMatch": "*",
        "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
    }
    stub.add_response("put_object", {}, upload)
    ref = store.put(
        workspace, "report", content, content_type="application/json", expected_sha256=digest
    )
    stub.add_client_error(
        "put_object", "PreconditionFailed", http_status_code=412, expected_params=upload
    )
    stub.add_response(
        "head_object",
        {
            "ContentLength": len(content),
            "ContentType": "application/json",
            "Metadata": {"sha256": digest},
        },
        params(workspace),
    )
    stub.add_response(
        "get_object",
        {
            "ContentLength": len(content),
            "Metadata": {"sha256": digest},
            "Body": StreamingBody(io.BytesIO(content), len(content)),
        },
        params(workspace),
    )
    assert (
        store.put(
            workspace, "report", content, content_type="application/json", expected_sha256=digest
        )
        == ref
    )


def test_source_upload_accepts_charset_parameter_spacing(setup):
    store, stub, workspace = setup
    content = b"def signal(close):\n    return close[-1]\n"
    digest = sha256(content).hexdigest()
    media_type = "text/x-python; charset=utf-8"
    stub.add_response(
        "put_object",
        {},
        {
            **params(workspace, "source.py"),
            "Body": content,
            "ContentType": media_type,
            "Metadata": {"sha256": digest},
            "IfNoneMatch": "*",
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
        },
    )
    assert store.put(
        workspace, "source.py", content, content_type=media_type, expected_sha256=digest
    ).content_type == media_type


@pytest.mark.parametrize("media_type", [" ", "text/plain\r\nX-Test: injected", "text/plain\x00"])
def test_content_type_rejects_blank_or_header_control_characters(setup, media_type):
    store, _stub, workspace = setup
    with pytest.raises(PortError, match="invalid content type"):
        store.put(
            workspace, "source.py", b"x", content_type=media_type,
            expected_sha256=sha256(b"x").hexdigest(),
        )


@pytest.mark.parametrize("method", ["get", "stat", "put"])
@pytest.mark.parametrize(
    "remote_code,expected",
    [
        ("NoSuchKey", ErrorCode.NOT_FOUND),
        ("AccessDenied", ErrorCode.UNAUTHORIZED),
        ("SlowDown", ErrorCode.RATE_LIMITED),
        ("RequestTimeout", ErrorCode.TIMEOUT),
        ("BadDigest", ErrorCode.CHECKSUM_MISMATCH),
        ("InternalError", ErrorCode.UNAVAILABLE),
        ("NotImplemented", ErrorCode.UNSUPPORTED),
        ("ConditionalRequestConflict", ErrorCode.UNAVAILABLE),
    ],
)
def test_sdk_errors_are_safe_and_standardized(setup, method, remote_code, expected):
    store, stub, workspace = setup
    sdk_method = {"get": "get_object", "stat": "head_object", "put": "put_object"}[method]
    stub.add_client_error(sdk_method, remote_code, service_message="secret-provider-response")
    with pytest.raises(PortError) as caught:
        if method == "get":
            store.get(workspace, "report", expected_sha256="a" * 64)
        elif method == "stat":
            store.stat(workspace, "report")
        else:
            store.put(
                workspace,
                "report",
                b"test",
                content_type="text/plain",
                expected_sha256=sha256(b"test").hexdigest(),
            )
    assert caught.value.code == expected
    assert "secret-provider-response" not in str(caught.value)


@pytest.mark.parametrize(
    "metadata,content",
    [
        ({}, b"data"),
        ({"sha256": "a" * 64}, b"data"),
        ({"sha256": sha256(b"data").hexdigest()}, b"evil"),
    ],
)
def test_download_checks_metadata_and_bytes(setup, metadata, content):
    store, stub, workspace = setup
    stub.add_response(
        "get_object",
        {
            "ContentLength": len(content),
            "Metadata": metadata,
            "Body": StreamingBody(io.BytesIO(content), len(content)),
        },
        params(workspace),
    )
    with pytest.raises(PortError) as caught:
        store.get(workspace, "report", expected_sha256=sha256(b"data").hexdigest())
    assert caught.value.code == ErrorCode.CHECKSUM_MISMATCH


def test_stat_rejects_untracked_objects(setup):
    store, stub, workspace = setup
    stub.add_response("head_object", {"ContentLength": 4, "Metadata": {}}, params(workspace))
    with pytest.raises(PortError) as caught:
        store.stat(workspace, "report")
    assert caught.value.code == ErrorCode.CHECKSUM_MISMATCH


def test_retry_conflicts_with_changed_content(setup):
    store, stub, workspace = setup
    stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
    stub.add_response(
        "head_object",
        {
            "ContentLength": 3,
            "ContentType": "text/plain",
            "Metadata": {"sha256": sha256(b"old").hexdigest()},
        },
        params(workspace),
    )
    with pytest.raises(PortError) as caught:
        store.put(
            workspace,
            "report",
            b"new",
            content_type="text/plain",
            expected_sha256=sha256(b"new").hexdigest(),
        )
    assert caught.value.code == ErrorCode.CONFLICT


@pytest.mark.parametrize("key", ["", "../escape", "/absolute", "a//b", "a\\b", "a\nb"])
def test_invalid_keys_never_touch_sdk(setup, key):
    store, _, workspace = setup
    with pytest.raises(PortError) as caught:
        store.stat(workspace, key)
    assert caught.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///secret",
        "http://objects.invalid",
        "https://user:password@host",
        "https://host/path",
        "https://host?token=secret",
    ],
)
def test_endpoint_validation(endpoint):
    with pytest.raises(PortError) as caught:
        S3ObjectStore.connect(bucket="test", endpoint_url=endpoint)
    assert caught.value.code == ErrorCode.INVALID_INPUT


def test_connection_and_timeout_errors():
    assert (
        translated_error(EndpointConnectionError(endpoint_url="secret")).code
        == ErrorCode.UNAVAILABLE
    )
    assert translated_error(ReadTimeoutError(endpoint_url="secret")).code == ErrorCode.TIMEOUT
