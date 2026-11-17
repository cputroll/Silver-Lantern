import hashlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from test_game_sync import h


class ParallelDownloadTests(unittest.TestCase):
    def test_ranges_and_integrity(self):
        data = b'abcdefgh' * (1024 * 1024)
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                value = self.headers.get('Range')
                requests.append(value)
                a, b = map(int, value[6:].split('-'))
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {a}-{b}/{len(data)}')
                self.send_header('Content-Length', str(b-a+1))
                self.end_headers()
                self.wfile.write(data[a:b+1])
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'asset'
            progress = h.Progress('test', len(data), tty=False)
            h.parallel_download(h.session, f'http://127.0.0.1:{server.server_port}/', dest,
                                len(data), hashlib.sha256(data).hexdigest(), progress, 2,
                                chunk_min=1)
            self.assertEqual(dest.read_bytes(), data)
            self.assertEqual(len(requests), 2)
            self.assertEqual(progress.done, len(data))
