"""起一个只读静态服务器托管 demo-app，供本地运行与评测使用。"""

from __future__ import annotations

import functools
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - 父类签名
        return


def start_server(root: str | Path, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    directory = str(Path(root).resolve())
    handler = functools.partial(_QuietHandler, directory=directory)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    return server, url


def stop_server(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


@contextmanager
def running_server(root: str | Path, port: int = 0) -> Generator[str, None, None]:
    server, url = start_server(root, port)
    try:
        yield url
    finally:
        stop_server(server)
