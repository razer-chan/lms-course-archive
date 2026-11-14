"""Synthetic localhost tests only; never requests an LMS or other remote host."""
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import quote

SCRIPT = Path(__file__).parents[1] / "skills/lms-course-archive/scripts/capture_receiver.py"
spec = importlib.util.spec_from_file_location("capture_receiver", SCRIPT)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)
ORIGIN = "https://lms.example.invalid"
TOKEN = "synthetic-test-token-not-a-real-secret-12345"


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="receiver-test-")
        self.root = Path(self.temp.name) / "archive"
        self.server = receiver.CaptureServer(self.root, [ORIGIN], TOKEN, max_bytes=64, read_timeout=1)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method="POST", endpoint="/save", body=b"hello", path="unit/file.txt", origin=ORIGIN, token=TOKEN, headers=None):
        supplied = {"Origin": origin, "X-Capture-Token": token, "X-Capture-Path": quote(path, safe="")}
        supplied.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, endpoint, body=body, headers=supplied)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), json.loads(data) if data else None)
        connection.close()
        return result

    def test_save_idempotent_conflict_and_exists(self):
        status, headers, result = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "saved")
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(result["sha256"], hashlib.sha256(b"hello").hexdigest())
        self.assertEqual((self.root / "unit/file.txt").read_bytes(), b"hello")
        self.assertEqual(self.request()[2]["status"], "already_identical")
        self.assertEqual(self.request(body=b"changed")[0], 409)
        self.assertEqual((self.root / "unit/file.txt").read_bytes(), b"hello")
        status, _, result = self.request("GET", "/exists?path=unit%2Ffile.txt", body=None)
        self.assertEqual(status, 200)
        self.assertTrue(result["exists"])

    def test_origin_token_and_host_are_exact(self):
        for origin in ("https://lms.example.invalid.evil.test", "null", "http://lms.example.invalid", ""):
            status, headers, _ = self.request(origin=origin)
            self.assertEqual(status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request(token="wrong")[0], 403)
        self.assertEqual(self.request(headers={"Host": "attacker.example.invalid"})[0], 403)
        self.assertFalse((self.root / "unit/file.txt").exists())

    def test_preflight_checks_origin_headers_and_private_network(self):
        headers = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-capture-token,x-capture-path,content-type",
                   "Access-Control-Request-Private-Network": "true"}
        status, response_headers, _ = self.request("OPTIONS", body=None, headers=headers)
        self.assertEqual(status, 204)
        self.assertEqual(response_headers["Access-Control-Allow-Private-Network"], "true")
        headers["Access-Control-Request-Headers"] = "X-Overwrite"
        self.assertEqual(self.request("OPTIONS", body=None, headers=headers)[0], 403)

    def test_traversal_absolute_backslash_and_symlinks_rejected(self):
        for path in ("../escape.txt", "/escape.txt", "x/../../escape.txt", "x\\escape.txt", "a//b", "a/./b", "C:/file", "NUL.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path)[0], 400)
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.request(path="link/escape.txt")[0], 400)
        (self.root / "target.txt").symlink_to(outside / "missing.txt")
        self.assertEqual(self.request(path="target.txt")[0], 400)
        self.assertFalse((outside / "escape.txt").exists())

    def test_limits_content_length_and_incomplete_body(self):
        self.assertEqual(self.request(body=b"x" * 65)[0], 413)
        self.assertEqual(self.request(headers={"Content-Length": "nonsense"})[0], 411)
        self.assertEqual(self.request(headers={"Transfer-Encoding": "chunked"})[0], 411)
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3) as sock:
            text = f"POST /save HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\nOrigin: {ORIGIN}\r\nX-Capture-Token: {TOKEN}\r\nX-Capture-Path: short.txt\r\nContent-Length: 10\r\n\r\nabc"
            sock.sendall(text.encode())
            sock.shutdown(socket.SHUT_WR)
            self.assertIn(b"400", sock.recv(4096))
        self.assertFalse((self.root / "short.txt").exists())
        self.assertEqual(list(self.root.rglob(".capture-*")), [])

    def test_shutdown_is_authenticated(self):
        self.assertEqual(self.request(endpoint="/shutdown", body=b"", token="wrong")[0], 403)
        self.assertTrue(self.thread.is_alive())
        self.assertEqual(self.request(endpoint="/shutdown", body=b"")[0], 200)
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def test_cli_fresh_private_token_not_printed_and_cleanup(self):
        token_file = Path(self.temp.name) / "private/token.json"
        process = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "--root", str(Path(self.temp.name) / "cli-output"),
                                    "--origin", ORIGIN, "--token-file", str(token_file)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready_line = process.stdout.readline()
            ready = json.loads(ready_line)
            private = json.loads(token_file.read_text())
            self.assertNotIn(private["token"], ready_line)
            self.assertNotEqual(private["token"], TOKEN)
            if os.name == "posix":
                self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
            port = int(ready["receiver"].rsplit(":", 1)[1])
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("POST", "/shutdown", body=b"", headers={"Origin": ORIGIN, "X-Capture-Token": private["token"]})
            self.assertEqual(connection.getresponse().status, 200)
            connection.close()
            _, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertNotIn(private["token"], stderr)
            self.assertFalse(token_file.exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
