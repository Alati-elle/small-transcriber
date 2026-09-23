"""macOS curl proof of concept: pass a header via an inherited anonymous pipe."""

import os
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer


class InheritedHeaderFdTest(unittest.TestCase):
    def test_curl_reads_header_from_pipe_without_argv_or_disk_secret(self):
        key = "FAKE_FD_KEY_784321"
        payload = b'{"synthetic":"FAKE_FD_PAYLOAD_628419"}'
        received = {}
        arrived = threading.Event()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received["key"] = self.headers.get("x-goog-api-key")
                received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
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
        read_fd, write_fd = os.pipe()
        process = None
        outcome = {}
        try:
            os.write(write_fd, ("x-goog-api-key: " + key + "\n").encode())
            os.close(write_fd)
            write_fd = None
            process = subprocess.Popen(
                [
                    "curl", "-4", "-sS",
                    f"http://127.0.0.1:{server.server_port}",
                    "-H", f"@/dev/fd/{read_fd}",
                    "--data-binary", "@-",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(read_fd,),
            )
            os.close(read_fd)
            read_fd = None

            def finish_request():
                outcome["stdout"], outcome["stderr"] = process.communicate(
                    input=payload, timeout=15
                )

            worker = threading.Thread(target=finish_request)
            worker.start()
            self.assertTrue(arrived.wait(10), "local curl request did not arrive")
            command = subprocess.run(
                ["ps", "-ww", "-p", str(process.pid), "-o", "command="],
                capture_output=True, text=True, check=True,
            ).stdout
            self.assertIn(f"@/dev/fd/", command)
            self.assertNotIn(key, command)
            self.assertNotIn(payload.decode(), command)
            self.assertEqual(received, {"key": key, "body": payload})
        finally:
            release.set()
            if process is not None:
                process.wait(timeout=15)
            if read_fd is not None:
                os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
            server.shutdown()
            server_thread.join(10)
            server.server_close()
        worker.join(10)
        self.assertEqual(process.returncode, 0, outcome.get("stderr"))
        self.assertEqual(outcome["stdout"], b"{}")


if __name__ == "__main__":
    unittest.main()
