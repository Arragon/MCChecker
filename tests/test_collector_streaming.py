"""M16-22 bounded stream/staging tests."""

import hashlib
import os
import threading

import pytest

from collector.stream_reader import (
    StagingSink,
    StreamError,
    read_limited,
    verify_received,
)


class ChunkStream:
    def __init__(self, chunks, clock=None, step=0.0):
        self.chunks = list(chunks)
        self.index = 0
        self.clock = clock
        self.step = step

    def read(self, size):
        if self.clock is not None:
            self.clock.value += self.step
        if self.index >= len(self.chunks):
            return b""
        chunk = self.chunks[self.index]
        self.index += 1
        return chunk


class Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class Sink:
    def __init__(self):
        self.data = bytearray()
        self.aborted = False

    def write(self, data):
        self.data.extend(data)

    def abort(self):
        self.aborted = True


def test_read_1mb_and_verify():
    data = os.urandom(1024 * 1024)
    stream = ChunkStream([data[:300000], data[300000:700000], data[700000:]])
    sink = Sink()
    result = read_limited(stream, sink)
    assert result["size"] == len(data)
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert bytes(sink.data) == data
    verify_received(bytes(sink.data), result["size"], result["sha256"])


def test_limit_deadline_cancel_and_errors():
    sink = Sink()
    with pytest.raises(StreamError) as exc:
        read_limited(ChunkStream([b"x" * 200]), sink, limit_bytes=100)
    assert exc.value.reason_code == "too_large"
    assert sink.aborted

    clock = Clock(0.0)
    sink = Sink()
    with pytest.raises(StreamError) as exc:
        read_limited(
            ChunkStream([b"a", b"b"], clock=clock, step=2.0),
            sink,
            deadline=1.0,
            clock=clock,
        )
    assert exc.value.reason_code == "collector_timeout"
    assert sink.aborted

    event = threading.Event()
    event.set()
    sink = Sink()
    with pytest.raises(StreamError) as exc:
        read_limited(ChunkStream([b"a"]), sink, cancel_event=event)
    assert exc.value.reason_code == "canceled"
    assert sink.aborted

    sink = Sink()
    with pytest.raises(StreamError) as exc:
        read_limited(ChunkStream(["not-bytes"]), sink)
    assert exc.value.reason_code == "invalid_payload"
    assert sink.aborted


def test_infinite_stream_is_bounded():
    class Infinite:
        def read(self, size):
            return b"x" * size

    sink = Sink()
    with pytest.raises(StreamError) as exc:
        read_limited(Infinite(), sink, limit_bytes=1024 * 1024, chunk_size=64 * 1024)
    assert exc.value.reason_code == "too_large"
    assert sink.aborted


def test_staging_sink_commit_and_abort(tmp_path):
    sink = StagingSink(str(tmp_path / "stage"))
    sink.write(b"abc")
    path = sink.commit()
    assert os.path.exists(path)
    with open(path, "rb") as handle:
        assert handle.read() == b"abc"

    sink2 = StagingSink(str(tmp_path / "stage"))
    path2 = sink2.path
    sink2.write(b"partial")
    sink2.abort()
    assert not os.path.exists(path2)

    sink3 = StagingSink(str(tmp_path / "stage"))
    path3 = sink3.path
    with pytest.raises(StreamError):
        read_limited(ChunkStream([b"x" * 100]), sink3, limit_bytes=10)
    assert not os.path.exists(path3)


def test_verify_received_rejects_size_and_hash_mismatch():
    with pytest.raises(StreamError):
        verify_received(b"abc", 2, hashlib.sha256(b"abc").hexdigest())
    with pytest.raises(StreamError):
        verify_received(b"abc", 3, "0" * 64)
    with pytest.raises(StreamError):
        verify_received("abc", 3, hashlib.sha256(b"abc").hexdigest())
