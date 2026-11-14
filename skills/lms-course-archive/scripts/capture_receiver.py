#!/usr/bin/env python3
"""Optional localhost receiver for an explicitly authorized browser capture.

Python 3.10+ standard library. No remote requests. Does not fetch pages, inspect
cookies, or transcribe media. Keep the output root private while this process runs.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import socket
import stat
import sys
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlsplit


def exact_origin(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("origin must be an exact http(s) origin without credentials")
    if parsed.path or parsed.query or parsed.fragment or value != f"{parsed.scheme}://{parsed.netloc}":
        raise ValueError("origin must contain only scheme and host[:port], without a trailing slash")
    return value


def path_for(root, relative):
    if not isinstance(relative, str) or not relative or any(ord(c) < 32 for c in relative):
        raise ValueError("invalid relative path")
    if "\\" in relative or ":" in relative or relative.startswith("/"):
        raise ValueError("absolute/platform-special paths are not accepted")
    pieces = relative.split("/")
    if any(piece in {"", ".", ".."} or piece.endswith((".", " ")) for piece in pieces):
        raise ValueError("empty, traversal or ambiguous path component")
    if any(re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p, re.I) for p in pieces):
        raise ValueError("reserved platform path")
    target = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    for piece in pieces:
        current /= piece
        if current.is_symlink():
            raise ValueError("symlink paths are not accepted")
    if not target.resolve().is_relative_to(root):
        raise ValueError("path escapes capture root")
    return target


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = False  # server_close waits for in-flight writes to finish.
    allow_reuse_address = False

    def __init__(self, root, origins, token, port=0, max_bytes=256 * 1024 * 1024, read_timeout=15):
        path = Path(root).expanduser().absolute()
        if path.is_symlink():
            raise ValueError("capture root itself must not be a symlink")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = path.resolve()
        self.origins = {exact_origin(origin) for origin in origins}
        if not self.origins:
            raise ValueError("at least one exact origin is required")
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("a fresh random token of at least 32 characters is required")
        if not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.token = token
        self.max_bytes = max_bytes
        self.read_timeout = read_timeout
        self.write_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), CaptureHandler)


class CaptureHandler(BaseHTTPRequestHandler):
    server_version = "LocalCapture/1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.read_timeout)

    def log_message(self, *_):
        # Never log URLs, paths, request headers, cookies, tokens or response bodies.
        pass

    def one_header(self, key):
        values = self.headers.get_all(key, [])
        return values[0] if len(values) == 1 else None

    def allowed_origin(self):
        origin = self.one_header("Origin")
        return origin if origin in self.server.origins else None

    def host_ok(self):
        return self.one_header("Host") == f"127.0.0.1:{self.server.server_port}"

    def auth(self):
        if not self.host_ok() or not self.allowed_origin():
            self.reply(403, {"ok": False, "error": "origin_or_host_rejected"})
            return False
        token = self.one_header("X-Capture-Token") or ""
        if not secrets.compare_digest(token, self.server.token):
            self.reply(403, {"ok": False, "error": "authentication_required"})
            return False
        return True

    def cors(self):
        origin = self.allowed_origin()
        if origin and self.host_ok():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Capture-Token, X-Capture-Path")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")

    def reply(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        requested = {s.strip().lower() for s in self.headers.get("Access-Control-Request-Headers", "").split(",") if s.strip()}
        permitted = {"content-type", "x-capture-token", "x-capture-path"}
        if (not self.host_ok() or not self.allowed_origin() or not requested <= permitted
                or self.headers.get("Access-Control-Request-Method") not in {"GET", "POST"}):
            return self.reply(403, {"ok": False, "error": "preflight_rejected"})
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self.auth():
            return
        parsed = urlsplit(self.path)
        if parsed.path != "/exists":
            return self.reply(404, {"ok": False, "error": "unknown_endpoint"})
        params = parse_qs(parsed.query, keep_blank_values=True)
        if set(params) != {"path"} or len(params["path"]) != 1:
            return self.reply(400, {"ok": False, "error": "one_relative_path_required"})
        try:
            target = path_for(self.server.root, params["path"][0])
            if target.exists() and not target.is_file():
                raise ValueError("path is not a regular file")
            self.reply(200, {"ok": True, "exists": target.is_file(), "bytes": target.stat().st_size if target.is_file() else None,
                             "sha256": hash_file(target) if target.is_file() else None})
        except (ValueError, OSError):
            self.reply(400, {"ok": False, "error": "unsafe_or_unreadable_path"})

    def body_length(self):
        raw = self.one_header("Content-Length")
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("transfer_encoding_not_supported")
        if raw is None or not re.fullmatch(r"[0-9]+", raw):
            raise ValueError("content_length_required")
        size = int(raw)
        if size > self.server.max_bytes:
            raise OverflowError("body_exceeds_max_bytes")
        return size

    def do_POST(self):
        if not self.auth():
            return
        if self.path not in {"/save", "/shutdown"}:
            return self.reply(404, {"ok": False, "error": "unknown_endpoint"})
        try:
            size = self.body_length()
        except ValueError as exc:
            return self.reply(411, {"ok": False, "error": str(exc)})
        except OverflowError as exc:
            return self.reply(413, {"ok": False, "error": str(exc)})
        if self.path == "/shutdown":
            if size:
                return self.reply(400, {"ok": False, "error": "shutdown_body_must_be_empty"})
            self.reply(200, {"ok": True, "stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        temporary = None
        try:
            encoded = self.one_header("X-Capture-Path")
            if encoded is None:
                raise ValueError("path_header_required")
            relative = unquote(encoded, errors="strict")
            target = path_for(self.server.root, relative)
            with self.server.write_lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                target = path_for(self.server.root, relative)
                if target.exists() and not stat.S_ISREG(target.stat().st_mode):
                    raise ValueError("not_a_regular_file")
                with tempfile.NamedTemporaryFile(prefix=".capture-", dir=target.parent, delete=False) as out:
                    temporary = Path(out.name)
                    remaining, digest = size, hashlib.sha256()
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("incomplete_body")
                        out.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                target = path_for(self.server.root, relative)
                status = "saved"
                try:
                    # Hard-link creation is atomic and cannot replace any existing target.
                    os.link(temporary, target)
                except FileExistsError:
                    if target.is_symlink() or not target.is_file():
                        raise ValueError("not_a_regular_file")
                    if target.stat().st_size != size or hash_file(target) != digest.hexdigest():
                        return self.reply(409, {"ok": False, "error": "existing_file_differs"})
                    status = "already_identical"
                self.reply(200, {"ok": True, "status": status, "bytes": size, "sha256": digest.hexdigest(), "path": relative})
        except (ValueError, UnicodeError):
            self.reply(400, {"ok": False, "error": "unsafe_path_or_incomplete_body"})
        except (socket.timeout, TimeoutError):
            self.reply(408, {"ok": False, "error": "body_read_timeout"})
        except OSError:
            self.reply(500, {"ok": False, "error": "local_write_failed"})
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--origin", required=True, action="append")
    parser.add_argument("--token-file", required=True, type=Path, help="New private file outside capture root; deleted at shutdown")
    parser.add_argument("--port", type=int, default=0, help="0 chooses an unused local port")
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024, help="Maximum single request size")
    args = parser.parse_args()
    token_path = args.token_file.expanduser().absolute()
    server = None
    token_identity = None
    try:
        server = CaptureServer(args.root, args.origin, secrets.token_urlsafe(32), args.port, args.max_bytes)
        if token_path.is_symlink() or token_path.resolve().is_relative_to(server.root):
            raise ValueError("token-file must be outside capture root and must not be a symlink")
        token_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(token_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"receiver": f"http://127.0.0.1:{server.server_port}", "token": server.token,
                       "origins": sorted(server.origins), "valid_until": "receiver_process_exit"}, stream)
            token_identity = os.fstat(stream.fileno()).st_ino
        def stop(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        print(json.dumps({"ready": True, "receiver": f"http://127.0.0.1:{server.server_port}",
                          "root": str(server.root), "token_file": str(token_path), "pid": os.getpid()}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    except (ValueError, OSError) as exc:
        print("Receiver startup failed: " + str(exc), file=sys.stderr)
        return 1
    finally:
        if server:
            server.server_close()
        if token_identity is not None:
            try:
                if not token_path.is_symlink() and token_path.stat().st_ino == token_identity:
                    token_path.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
