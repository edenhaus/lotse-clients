"""Fixtures: a scripted fake daemon on a Unix socket, and the real daemon when built."""

import asyncio
import os
import shutil
import socket
import tempfile
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def serving(rocko_bin: Path, socket_dir: Path, *flags: str | Path) -> AsyncIterator[Path]:
    """`rocko serve` on a socket in `socket_dir`, with `flags` added; yields the socket path."""
    path = socket_dir / "rocko.sock"
    process = await asyncio.create_subprocess_exec(
        rocko_bin,
        "serve",
        "--socket",
        path,
        "--sandbox",
        "off",
        "--webrtc-udp-listen",
        "127.0.0.1:0",
        "--webrtc-tcp-listen",
        "127.0.0.1:0",
        "--linger-ms",
        "100",
        *flags,
        env={},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(10):
            while not path.exists():
                assert process.returncode is None, "rocko serve exited"
                await asyncio.sleep(0.02)
        yield path
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                async with asyncio.timeout(10):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()


@pytest_asyncio.fixture
async def daemon(rocko_bin: Path, socket_dir: Path) -> AsyncIterator[Path]:
    """A real `rocko serve` on a private socket; yields the socket path."""
    async with serving(rocko_bin, socket_dir) as path:
        yield path


@pytest_asyncio.fixture
async def remote_daemon(rocko_bin: Path, socket_dir: Path) -> AsyncIterator[tuple[Path, int]]:
    """A real `rocko serve` with the control TLS listener on 127.0.0.1 too, no client enrolled.

    Yields the socket path and the listener's port. The data directory, where
    the daemon keeps its identity and the enrolled pins, is private (0700) as
    `serve` requires. The listener is bound before the socket answers, so a
    client that read `hello` on the socket finds it.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    data = socket_dir / "data"
    data.mkdir(mode=0o700)
    async with serving(
        rocko_bin, socket_dir, "--control-listen", f"127.0.0.1:{port}", "--data-dir", data
    ) as path:
        yield path, port
