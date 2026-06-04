"""Unit tests for the persistent-terminal sentinel protocol -- the fragile bit --
exercised in isolation with a fake stream, no Docker required.
"""

import asyncio

import pytest

from miniharbor.environment.base import SandboxError
from miniharbor.environment.docker import _drain_until


def _reader(data: bytes) -> asyncio.StreamReader:
    r = asyncio.StreamReader()
    r.feed_data(data)
    r.feed_eof()
    return r


async def test_parses_output_and_exit_code():
    r = _reader(b"hello\nworld\n\n__MH_DONE_abc__0\n")
    out, code = await _drain_until(r, "__MH_DONE_abc__")
    assert "hello" in out and "world" in out
    assert code == 0


async def test_nonzero_exit_code():
    r = _reader(b"boom\n__MH_DONE_x__1\n")
    out, code = await _drain_until(r, "__MH_DONE_x__")
    assert code == 1
    assert "boom" in out


async def test_marker_midline_splits_output():
    r = _reader(b"partial__MH_DONE_x__7\n")
    out, code = await _drain_until(r, "__MH_DONE_x__")
    assert out == "partial"
    assert code == 7


async def test_eof_before_marker_raises():
    r = _reader(b"no marker here\n")
    with pytest.raises(SandboxError):
        await _drain_until(r, "__MH_DONE_x__")
