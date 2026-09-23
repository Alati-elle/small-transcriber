"""Synthetic checks only; never contact Gemini or read the Keychain."""

import contextlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_source(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecurityAndProtocolTests(unittest.TestCase):
    def test_key_and_payload_absent_from_curl_argv(self):
        key = "FAKE_TEST_KEY_argv_marker_49382"
        payload = '{"synthetic":"FAKE_PAYLOAD_argv_marker_57391"}'

        for name in ("gemini_transcribe_meeting", "gemini_make_protocol"):
            with self.subTest(module=name):
                received = {}
                arrived = threading.Event()
                release = threading.Event()

                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self):
                        received["key"] = self.headers.get("x-goog-api-key")
                        received["body"] = self.rfile.read(
                            int(self.headers["Content-Length"])
                        ).decode("utf-8")
                        arrived.set()
                        release.wait(10)
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b"{}")

                    def log_message(self, *_):
                        pass

                server = HTTPServer(("127.0.0.1", 0), Handler)
                server_thread = threading.Thread(target=server.serve_forever)
                server_thread.start()
                outcome = {}
                port = server.server_port

                def request():
                    try:
                        outcome["response"] = load_source(name).run_gemini_curl(
                            ["curl", "-4", "-sS", f"http://127.0.0.1:{port}"],
                            key,
                            payload,
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                    except Exception as error:
                        outcome["error"] = error

                worker = threading.Thread(target=request)
                worker.start()
                try:
                    self.assertTrue(arrived.wait(10), "local curl request did not arrive")
                    listing = subprocess.run(
                        ["ps", "-ww", "-A", "-o", "command="],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                    curl_lines = [
                        line for line in listing.splitlines()
                        if "curl" in line and f"127.0.0.1:{port}" in line
                    ]
                    self.assertEqual(len(curl_lines), 1, curl_lines)
                    argv = shlex.split(curl_lines[0])
                    self.assertNotIn(key, curl_lines[0])
                    self.assertNotIn(payload, curl_lines[0])
                    self.assertIn("--data-binary", argv)
                    self.assertEqual(argv[argv.index("--data-binary") + 1], "@-")
                    header_ref = argv[argv.index("-H") + 1]
                    self.assertRegex(header_ref, r"^@/dev/fd/\d+$")
                finally:
                    release.set()
                    worker.join(10)
                    server.shutdown()
                    server_thread.join(10)
                    server.server_close()

                self.assertNotIn("error", outcome)
                self.assertEqual(received, {"key": key, "body": payload})
                self.assertEqual(outcome["response"].stdout, "{}")

    def test_failed_generation_keeps_previous_protocol(self):
        protocol = load_source("gemini_make_protocol")
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "sample_ПОЛНАЯ_РАСШИФРОВКА.txt"
            transcript.write_text(
                "[00:00–00:02] Спикер 0:\nСинтетическая тестовая реплика.\n",
                encoding="utf-8",
            )
            json_path, html_path = protocol.output_paths(transcript)
            json_path.write_bytes(b"previous JSON")
            html_path.write_bytes(b"previous HTML")
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(sys, "argv", ["protocol", str(transcript)]))
                stack.enter_context(mock.patch.object(protocol, "get_key", return_value="FAKE_KEY"))
                stack.enter_context(mock.patch.object(
                    protocol, "consolidate_speakers", side_effect=lambda rows, _: (rows, [], None)
                ))
                stack.enter_context(mock.patch.object(
                    protocol, "detect_speakers", return_value=([], None)
                ))
                stack.enter_context(mock.patch.object(
                    protocol, "call_gemini", side_effect=RuntimeError("synthetic failure")
                ))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                    protocol.main()
            self.assertEqual(json_path.read_bytes(), b"previous JSON")
            self.assertEqual(html_path.read_bytes(), b"previous HTML")

    def test_success_replaces_both_protocol_files(self):
        protocol = load_source("gemini_make_protocol")
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "sample_ПРОТОКОЛ.json"
            html_path = Path(directory) / "sample_ПРОТОКОЛ.html"
            json_path.write_bytes(b"previous JSON")
            html_path.write_bytes(b"previous HTML")
            protocol.publish_protocol(json_path, html_path, {"synthetic": True}, "<html>new</html>")
            self.assertEqual(json.loads(json_path.read_text()), {"synthetic": True})
            self.assertEqual(html_path.read_text(), "<html>new</html>")
            self.assertEqual(list(Path(directory).glob(".protocol-*")), [])

    def test_second_replace_error_restores_both_previous_files(self):
        protocol = load_source("gemini_make_protocol")
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "sample_ПРОТОКОЛ.json"
            html_path = Path(directory) / "sample_ПРОТОКОЛ.html"
            json_path.write_bytes(b"previous JSON")
            html_path.write_bytes(b"previous HTML")
            real_replace = os.replace

            def fail_html_once(source, target):
                if target == html_path:
                    raise OSError("synthetic replacement failure")
                return real_replace(source, target)

            with mock.patch.object(protocol.os, "replace", side_effect=fail_html_once):
                with self.assertRaisesRegex(OSError, "synthetic replacement failure"):
                    protocol.publish_protocol(
                        json_path, html_path, {"synthetic": True}, "<html>new</html>"
                    )
            self.assertEqual(json_path.read_bytes(), b"previous JSON")
            self.assertEqual(html_path.read_bytes(), b"previous HTML")


if __name__ == "__main__":
    unittest.main()
