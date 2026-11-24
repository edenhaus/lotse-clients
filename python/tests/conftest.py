"""Fixtures: a scripted fake daemon on a Unix socket, and the real daemon when built."""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from tests.helpers import FakeDaemon

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A short private directory for a socket path (macOS caps them at 104 bytes)."""
    path = Path(tempfile.mkdtemp(prefix="rocko-py-"))
    path.chmod(0o700)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def fake(socket_dir: Path) -> AsyncIterator[FakeDaemon]:
    """A running fake daemon."""
    daemon = FakeDaemon(socket_dir / "fake.sock")
    await daemon.start()
    yield daemon
    await daemon.stop()


@pytest.fixture
def rocko_bin() -> Path:
    """The rocko binary named by `ROCKO_BIN`, built with the `source-fake` feature."""
    value = os.environ.get("ROCKO_BIN")
    if not value:
        pytest.skip("ROCKO_BIN is not set; the python-client-tests hook sets it")
    return Path(value)


@pytest_asyncio.fixture
async def daemon(rocko_bin: Path, socket_dir: Path) -> AsyncIterator[Path]:
    """A real `rocko serve` on a private socket; yields the socket path."""
    socket = socket_dir / "rocko.sock"
    process = await asyncio.create_subprocess_exec(
        rocko_bin,
        "serve",
        "--socket",
        socket,
        "--sandbox",
        "off",
        "--webrtc-udp-listen",
        "127.0.0.1:0",
        "--webrtc-tcp-listen",
        "127.0.0.1:0",
        "--linger-ms",
        "100",
        env={},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(10):
            while not socket.exists():
                assert process.returncode is None, "rocko serve exited"
                await asyncio.sleep(0.02)
        yield socket
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                async with asyncio.timeout(10):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
