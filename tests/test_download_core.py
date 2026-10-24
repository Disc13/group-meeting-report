"""Regression tests with local HTTP fixtures; no Tencent requests."""
import hashlib
import http.server
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import download_core as core
import tmdown


class DirectDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = bytes(range(256)) * 24
        cls.requests = []
        cls.fail = False
        outer = cls

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path)
                value = self.headers.get("Range")
                outer.requests.append((path.path, path.query, value))
                if path.path == "/expires" and path.query == "old" and value != "bytes=0-0":
                    self.send_error(410)
                    return
                start, end = 0, len(outer.blob)-1
                if value and path.path != "/stream":
                    start, end = map(int, value[6:].split("-"))
                if outer.fail and start == 2048:
                    self.send_error(503)
                    return
                body = outer.blob[start:end+1]
                ranged = value and path.path != "/stream"
                self.send_response(206 if ranged else 200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", '"fixture-v1"')
                if ranged:
                    range_start = start+1 if path.path == "/wrong" and start != end else start
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (range_start, end, len(outer.blob)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = str(Path(self.tmp.name) / "video.mp4")
        self.requests.clear()
        type(self).fail = False
        self.small_blocks = patch.object(core, "BLOCK_SIZE", 1024)
        self.small_blocks.start()
        self.addCleanup(self.small_blocks.stop)
        self.net = tmdown.Net(proxy="")

    def test_parallel_exact_bytes(self):
        core.download(self.net, self.base+"/file", self.output, threads=3)
        self.assertEqual(Path(self.output).read_bytes(), self.blob)
        self.assertFalse(os.path.exists(self.output+".state.json"))

    def test_ignored_range_streams(self):
        core.download(self.net, self.base+"/stream", self.output)
        self.assertEqual(Path(self.output).read_bytes(), self.blob)

    def test_wrong_content_range_cannot_publish(self):
        with patch.object(core.time, "sleep"), self.assertRaises(core.TransferError):
            core.download(self.net, self.base+"/wrong", self.output)
        self.assertFalse(os.path.exists(self.output))

    def test_expiration_single_refresh(self):
        calls = []
        def refresh():
            calls.append(True)
            return self.base+"/expires?new"
        core.download(self.net, self.base+"/expires?old", self.output,
                      threads=4, refresh=refresh, source_id="fixture")
        self.assertEqual(Path(self.output).read_bytes(), self.blob)
        self.assertEqual(len(calls), 1)

    def test_interruption_resume_changed_threads_and_corrupt_block(self):
        type(self).fail = True
        with patch.object(core.time, "sleep"), self.assertRaises(core.TransferError):
            core.download(self.net, self.base+"/file", self.output, threads=1)
        self.assertFalse(os.path.exists(self.output))
        state = json.loads(Path(self.output+".state.json").read_text())
        self.assertIn("0", state["done"])
        with open(self.output+".part", "r+b") as part:
            part.seek(0)
            part.write(b"corruption")
        type(self).fail = False
        self.requests.clear()
        core.download(self.net, self.base+"/file", self.output, threads=4)
        self.assertEqual(Path(self.output).read_bytes(), self.blob)
        ranges = [r[2] for r in self.requests]
        self.assertIn("bytes=0-1023", ranges)
        self.assertNotIn("bytes=1024-2047", ranges)

    def test_stale_identity_is_not_reused(self):
        Path(self.output+".part").write_bytes(b"x"*len(self.blob))
        done = {str(i): hashlib.sha256(b"x"*1024).hexdigest() for i in range(6)}
        core.atomic_json(self.output+".state.json", {
            "version": 2, "identity": "another-resource", "size": len(self.blob),
            "block_size": 1024, "validators": {"etag": '"fixture-v1"', "modified": None},
            "done": done})
        core.download(self.net, self.base+"/file", self.output)
        self.assertEqual(Path(self.output).read_bytes(), self.blob)

    def test_probe_never_reads_body(self):
        class HeadersOnly:
            headers = {"Content-Length": "1000000000"}
            def getcode(self): return 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): raise AssertionError("Probe read media body")
        with patch.object(core, "open_media", return_value=HeadersOnly()):
            self.assertEqual(core.probe_media(self.net, self.base)["size"], 1000000000)


class ParsingRegressionTests(unittest.TestCase):
    def test_tencent_requires_actual_hostname(self):
        self.assertFalse(tmdown.is_tencent_meeting("https://meeting.tencent.com.evil.test/cw/AbC123xyz"))
        self.assertFalse(tmdown.is_tencent_meeting("https://example.com/?url=https://meeting.tencent.com/cw/AbC123xyz"))

    def test_resign_keeps_password(self):
        with patch.object(tmdown, "tm_api_get", return_value={"url": "new"}) as call:
            tmdown.resign_tencent(None, {"record_id": "r", "resource_type": 1,
                                      "sharing_id": "s", "pwd": "password"})
            self.assertEqual(call.call_args[0][2]["pwd"], "password")


if __name__ == "__main__":
    unittest.main()
