"""Schannel stream against a local TLS server, including fragmented echo records."""
import asyncio
import contextlib
import unittest
from pathlib import Path
from relay_tls_fixture import RelayTLSFixture

class NativeTLSStreamTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):cls.tls=RelayTLSFixture()
    @classmethod
    def tearDownClass(cls):cls.tls.close()

    async def test_truncated_tls_stream_is_not_a_clean_close(self):
        exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_tls_stream_tests.exe'
        async def abort(reader,writer):
            await reader.readexactly(131111)
            writer.write(b'partial reply');await writer.drain()
            writer.transport.abort()  # Deliberately omit close_notify.
        server=await asyncio.start_server(abort,'127.0.0.1',0,ssl=self.tls.server)
        process=None
        try:
            process=await asyncio.create_subprocess_exec(str(exe),str(server.sockets[0].getsockname()[1]),str(self.tls.der),'trust','127.0.0.1',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            out,error=await asyncio.wait_for(process.communicate(),20)
            self.assertEqual(process.returncode,1)
            self.assertIn('without a close notification',error.decode(errors='replace'))
        finally:
            if process and process.returncode is None:process.kill();await process.wait()
            server.close();await server.wait_closed()

    async def test_native_encryption_and_certificate_rejection(self):
        exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_tls_stream_tests.exe'
        for trust,hostname in [('trust','127.0.0.1'),('untrusted','127.0.0.1'),('trust','localhost')]:
            with self.subTest(trust=trust,hostname=hostname):
                received=bytearray();tasks=set()
                async def echo(reader,writer):
                    task=asyncio.current_task();tasks.add(task)
                    try:
                        while len(received)<131111:
                            block=await reader.read(997)
                            if not block:break
                            received.extend(block);writer.write(block);await writer.drain()
                    except (ConnectionError,OSError):pass
                    finally:
                        writer.close()
                        with contextlib.suppress(ConnectionError,OSError):await writer.wait_closed()
                        tasks.discard(task)
                server=await asyncio.start_server(echo,'127.0.0.1',0,ssl=self.tls.server)
                process=None
                try:
                    port=server.sockets[0].getsockname()[1]
                    process=await asyncio.create_subprocess_exec(str(exe),str(port),str(self.tls.der),trust,hostname,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    out,error=await asyncio.wait_for(process.communicate(),20)
                    self.assertEqual(process.returncode,0,error.decode(errors='replace'))
                    if trust=='trust' and hostname=='127.0.0.1':self.assertEqual(received,bytes((i*31)%256 for i in range(131111)))
                    else:self.assertEqual(received,b'')
                finally:
                    if process and process.returncode is None:process.kill();await process.wait()
                    server.close();await server.wait_closed()
                    pending=tuple(tasks)
                    for task in pending:task.cancel()
                    await asyncio.gather(*pending,return_exceptions=True)

if __name__=='__main__':unittest.main()
