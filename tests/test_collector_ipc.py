"""M16-15 collector IPC protocol and client tests."""

import io
import os
import threading

import pytest

from app.services import collector_ipc as ipc
from app.services.collector_client import CollectorClient, CollectorClientError
from collector import ipc as collector_ipc


def test_protocol_constants_match():
    assert collector_ipc.PROTOCOL_VERSION == ipc.PROTOCOL_VERSION
    assert collector_ipc.MAX_FRAME_BYTES == ipc.MAX_FRAME_BYTES
    assert collector_ipc.MAX_CHUNK_BYTES == ipc.MAX_CHUNK_BYTES
    assert collector_ipc.MAX_CONTENT_BYTES == ipc.MAX_CONTENT_BYTES


def test_frame_roundtrip_and_oversize():
    payload = {"type": "request", "request_id": "req-1", "ok": True}
    raw = ipc.encode_frame(payload)
    assert ipc.read_frame(io.BytesIO(raw)) == payload
    assert ipc.decode_frame(raw) == payload

    bad = ipc.FRAME_HEADER.pack(ipc.MAX_FRAME_BYTES + 1)
    with pytest.raises(ipc.CollectorProtocolError):
        ipc.read_frame(io.BytesIO(bad + b"x"))
    with pytest.raises(ipc.CollectorProtocolError):
        ipc.decode_frame(ipc.FRAME_HEADER.pack(2) + b"[]")


def test_chunk_one_megabyte_roundtrip():
    data = os.urandom(1024 * 1024)
    frames = ipc.chunk_frames("req-1", data)
    assert len(frames) > 1
    assert frames[-1]["final"] is True
    assert frames[-1]["sha256"]
    assert ipc.reassemble_chunks(frames, "req-1") == data

    empty = ipc.chunk_frames("req-empty", b"")
    assert empty[-1]["size"] == 0
    assert ipc.reassemble_chunks(empty, "req-empty") == b""


def test_chunk_errors_are_rejected():
    data = b"abcdef" * 1000
    frames = ipc.chunk_frames("req-1", data, chunk_size=100)

    with pytest.raises(ipc.CollectorProtocolError):
        ipc.reassemble_chunks(frames[:-1], "req-1")

    duplicate = [dict(item) for item in frames]
    duplicate.insert(1, dict(duplicate[0]))
    with pytest.raises(ipc.CollectorProtocolError):
        ipc.reassemble_chunks(duplicate, "req-1")

    wrong = [dict(item) for item in frames]
    wrong[1]["seq"] = 99
    with pytest.raises(ipc.CollectorProtocolError):
        ipc.reassemble_chunks(wrong, "req-1")

    with pytest.raises(ipc.CollectorProtocolError):
        ipc.reassemble_chunks(frames, "req-other")

    bad_hash = [dict(item) for item in frames]
    bad_hash[-1]["sha256"] = "0" * 64
    with pytest.raises(ipc.CollectorProtocolError):
        ipc.reassemble_chunks(bad_hash, "req-1")


def _pipes():
    request_read, request_write = os.pipe()
    response_read, response_write = os.pipe()
    return (
        os.fdopen(request_read, "rb", buffering=0),
        os.fdopen(request_write, "wb", buffering=0),
        os.fdopen(response_read, "rb", buffering=0),
        os.fdopen(response_write, "wb", buffering=0),
    )


def test_client_reassembles_chunked_response():
    req_reader, req_writer, resp_reader, resp_writer = _pipes()
    client = CollectorClient(req_writer, resp_reader, "inst-1")
    data = b"<root>" + b"x" * 5000 + b"</root>"
    seen = {}

    def responder():
        request = ipc.read_frame(req_reader)
        seen.update(request)
        for frame in ipc.chunk_frames(request["request_id"], data, chunk_size=1024):
            ipc.write_frame(resp_writer, frame)

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    try:
        result = client.read_resource(
            "w-1",
            "conn-1",
            {"resource_ref": "res-1"},
            timeout=2,
            expected_policy_revision=3,
            expected_auth_epoch=2,
        )
        assert result == data
        assert seen["operation"] == "read_resource"
        assert seen["workspace_id"] == "w-1"
        assert seen["connection_id"] == "conn-1"
        assert seen["expected_policy_revision"] == 3
        assert seen["expected_auth_epoch"] == 2
        assert seen["protocol_version"] == ipc.PROTOCOL_VERSION
    finally:
        client.close()
        thread.join(2)
        req_reader.close()
        req_writer.close()
        resp_reader.close()
        resp_writer.close()


def test_client_timeout_and_cancel_frame():
    req_reader, req_writer, resp_reader, resp_writer = _pipes()
    client = CollectorClient(req_writer, resp_reader, "inst-1")
    try:
        with pytest.raises(CollectorClientError) as exc:
            client.request("keepalive", "w-1", "conn-1", timeout=0.05)
        assert exc.value.reason_code == "collector_timeout"

        request = ipc.read_frame(req_reader)
        assert request["type"] == "request"
        client.cancel(request["request_id"])
        frame = ipc.read_frame(req_reader)
        assert frame["type"] == "cancel"
        assert frame["request_id"] == request["request_id"]
    finally:
        client.close()
        req_reader.close()
        req_writer.close()
        resp_reader.close()
        resp_writer.close()


def test_client_error_frame_and_eof():
    req_reader, req_writer, resp_reader, resp_writer = _pipes()
    client = CollectorClient(req_writer, resp_reader, "inst-1")

    def responder():
        request = ipc.read_frame(req_reader)
        ipc.write_frame(
            resp_writer,
            {
                "type": "error",
                "request_id": request["request_id"],
                "reason_code": "auth_failed",
                "message": "synthetic auth failure",
            },
        )

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    try:
        with pytest.raises(CollectorClientError) as exc:
            client.request("ensure_session", "w-1", "conn-1", timeout=1)
        assert exc.value.reason_code == "auth_failed"
    finally:
        client.close()
        thread.join(2)
        req_reader.close()
        req_writer.close()
        resp_reader.close()
        resp_writer.close()

    req_reader, req_writer, resp_reader, resp_writer = _pipes()
    resp_writer.close()
    client = CollectorClient(req_writer, resp_reader, "inst-2")
    try:
        with pytest.raises(CollectorClientError) as exc:
            client.request("ensure_session", "w-1", "conn-1", timeout=0.5)
        assert exc.value.reason_code == "collector_offline"
    finally:
        client.close()
        req_reader.close()
        req_writer.close()
        resp_reader.close()


def test_client_rejects_unsupported_operation():
    req_reader, req_writer, resp_reader, resp_writer = _pipes()
    client = CollectorClient(req_writer, resp_reader, "inst-1")
    try:
        with pytest.raises(CollectorClientError):
            client.request("run_arbitrary_js", "w-1", "conn-1")
    finally:
        client.close()
        req_reader.close()
        req_writer.close()
        resp_reader.close()
        resp_writer.close()
