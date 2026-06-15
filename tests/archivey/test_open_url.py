import functools
import http.server
import logging
import os
import socket
import threading
from contextlib import contextmanager
from urllib.request import urlopen

import pytest
from tests.archivey.sample_archives import (
    ALTERNATIVE_CONFIG,
    BASIC_ARCHIVES,
    SINGLE_FILE_ARCHIVES,
    filter_archives,
)
from tests.archivey.test_open_nonseekable import EXPECTED_NON_SEEKABLE_FAILURES
from tests.archivey.testing_utils import skip_if_package_missing

from archivey.core import open_archive
from archivey.exceptions import ArchiveStreamNotSeekableError
from archivey.types import ArchiveFormat

logger = logging.getLogger(__name__)


class NoFQDNThreadingHTTPServer(http.server.ThreadingHTTPServer):
    # Custom HTTP server for tests.
    #
    # The stdlib `http.server.ThreadingHTTPServer` calls `socket.getfqdn(host)` in
    # `server_bind()`, which triggers a reverse DNS lookup. On macOS GitHub CI this
    # lookup can hang indefinitely for `localhost` / `127.0.0.1`, causing tests to
    # time out. This server overrides `server_bind()` to:
    #   • bind the socket directly,
    #   • set `server_address` from `getsockname()` (real port, not 0),
    #   • assign `server_name` without doing a reverse DNS lookup.
    #
    # This keeps behavior consistent across Linux, macOS, and Windows while avoiding
    # fragile DNS dependencies in tests.
    address_family = socket.AF_INET

    def server_bind(self) -> None:
        # Bind directly; set name/port without reverse DNS
        self.socket.bind(self.server_address)
        host, port = self.socket.getsockname()[:2]
        logger.info(f"Server bound to {host}:{port}")
        self.server_address = (host, port)
        # Use the literal host string as the server_name (no getfqdn)
        self.server_name = str(host)
        self.server_port = port


@contextmanager
def serve_dir(path: str):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=path)
    server = NoFQDNThreadingHTTPServer(("127.0.0.1", 0), handler)
    logger.info(f"Serving directory: {path}")
    logger.info(f"Dir contents: {list(os.listdir(path))}")

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://{server.server_name}:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        t.join()


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        BASIC_ARCHIVES + SINGLE_FILE_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format != ArchiveFormat.FOLDER,
    ),
    ids=lambda a: a.filename,
)
@pytest.mark.parametrize(
    "alternative_packages", [False, True], ids=["defaultlibs", "altlibs"]
)
def test_open_archive_via_http(sample_archive, alternative_packages):
    config = ALTERNATIVE_CONFIG if alternative_packages else None
    skip_if_package_missing(sample_archive.creation_info.format, config)

    path = sample_archive.get_archive_path()
    with serve_dir(os.path.dirname(path)) as base_url:
        url = f"{base_url}/{os.path.basename(path)}"
        logger.info(f"Opening URL: {url}")
        with urlopen(url, timeout=2) as response:
            try:
                with open_archive(
                    response, streaming_only=True, config=config
                ) as archive:
                    has_member = False
                    for member, stream in archive.iter_members_with_streams():
                        has_member = True
                        if stream is not None:
                            stream.read()
                    assert has_member
            except (
                ArchiveStreamNotSeekableError
            ) as exc:  # pragma: no cover - env dependent
                key = (sample_archive.creation_info.format, alternative_packages)
                if key in EXPECTED_NON_SEEKABLE_FAILURES:
                    pytest.xfail(
                        f"Non-seekable {sample_archive.creation_info.format} are not supported with {alternative_packages=}: {exc}"
                    )
                else:
                    assert False, (
                        f"Expected format {key} to work with HTTP streams, but it failed with {exc!r}"
                    )
