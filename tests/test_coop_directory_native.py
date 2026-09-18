"""Native WinHTTP client against a real local directory; requires built test binary."""
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from coop_directory import Server, Directory

class NativeDirectoryTest(unittest.TestCase):
    def test_rejects_invalid_responses(self):
        exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_directory_client_tests.exe'
        class Handler(BaseHTTPRequestHandler):
            payload=b''
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Length',str(len(self.payload)));self.end_headers()
                self.wfile.write(self.payload)
        with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                for payload in [b'not json',b'{"rooms":{}}',b'{"rooms":[{}]}',b'['*20+b'0'+b']'*20,b' '*131073]:
                    with self.subTest(payload_size=len(payload)):
                        Handler.payload=payload
                        result=subprocess.run([str(exe),f'http://127.0.0.1:{server.server_port}','reject'],capture_output=True,text=True,timeout=15)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            finally:server.shutdown();worker.join(5)

    def test_native_client(self):
        exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_directory_client_tests.exe'
        self.assertTrue(exe.exists(), 'Build aot_coop_directory_client_tests first')
        with Server(('127.0.0.1',0),Directory(relay_endpoint='tls://relay.example.test:37004')) as server:
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                result=subprocess.run([str(exe),f'http://127.0.0.1:{server.server_port}'],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                print(result.stdout.strip())
                result=subprocess.run([str(exe),f'http://127.0.0.1:{server.server_port}','worker'],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                print(result.stdout.strip())
            finally:
                server.shutdown();worker.join(5)

if __name__=='__main__':unittest.main()
