"""Two native Schannel/session processes through the actual TLS relay server."""
import asyncio
import contextlib
import json
import sys
import unittest
from pathlib import Path
from relay_tls_fixture import RelayTLSFixture
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from coop_rendezvous import Relay

class NativeRelaySessionTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):cls.tls=RelayTLSFixture()
    @classmethod
    def tearDownClass(cls):cls.tls.close()

    async def test_combined_handshake_and_game_bytes_are_preserved(self):
        await self.scripted_server(False)

    async def test_invalid_pairing_cannot_enable_game_bytes(self):
        await self.scripted_server(True)

    async def test_join_errors_explain_recovery_without_echoing_server_text(self):
        cases=[({'error':'room unavailable'},'This online game is no longer available.'),
               ({'error':'relay busy'},'The online service is full.'),
               ({'error':'room limit reached'},'The online service is full.'),
               ({'error':'PRIVATE_SERVER_TEXT'},'Could not join this online game.'),
               ({'error':'room unavailable','extra':'PRIVATE_SERVER_TEXT'},'Could not join this online game.')]
        exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_relay_session_tests.exe'
        for response,expected in cases:
            with self.subTest(response=response):
                received=bytearray();finished=asyncio.Event()
                async def handle(reader,writer):
                    try:
                        await reader.readline()
                        writer.write(json.dumps(response).encode()+b'\n');await writer.drain()
                        received.extend(await reader.read())
                    finally:
                        writer.close()
                        with contextlib.suppress(ConnectionError,OSError):await writer.wait_closed()
                        finished.set()
                server=await asyncio.start_server(handle,'127.0.0.1',0,ssl=self.tls.server)
                process=None
                try:
                    process=await asyncio.create_subprocess_exec(str(exe),str(server.sockets[0].getsockname()[1]),str(self.tls.der),'reject','b'*32,'c'*43,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    out,error=await asyncio.wait_for(process.communicate(),10)
                    await asyncio.wait_for(finished.wait(),5)
                    self.assertEqual(process.returncode,0,error.decode(errors='replace'))
                    self.assertIn(expected,error.decode())
                    self.assertNotIn('PRIVATE_SERVER_TEXT',error.decode())
                    self.assertEqual(received,b'')
                finally:
                    if process and process.returncode is None:process.kill();await process.wait()
                    server.close();await server.wait_closed()

    async def scripted_server(self,invalid):
        exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_relay_session_tests.exe'
        payload=bytes((i*31)%256 for i in range(131111));received=bytearray();tasks=set()
        async def handle(reader,writer):
            task=asyncio.current_task();tasks.add(task)
            try:
                await reader.readline()
                registration=json.dumps({'version':1,'room':'b'*32,'join_token':'c'*43}).encode()+b'\n'
                paired=b'{"version":1,"paired":1}\n' if invalid else b'{"version":1,"paired":true}\n'+payload
                writer.write(registration+paired);await writer.drain()
                if invalid:received.extend(await reader.read())
                else:received.extend(await reader.readexactly(len(payload)))
            finally:
                writer.close()
                with contextlib.suppress(ConnectionError,OSError):await writer.wait_closed()
                tasks.discard(task)
        server=await asyncio.start_server(handle,'127.0.0.1',0,ssl=self.tls.server)
        process=None
        try:
            process=await asyncio.create_subprocess_exec(str(exe),str(server.sockets[0].getsockname()[1]),str(self.tls.der),'host',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            out,error=await asyncio.wait_for(process.communicate(),10)
            self.assertEqual(process.returncode,1 if invalid else 0,error.decode(errors='replace'))
            self.assertEqual(received,b'' if invalid else payload)
        finally:
            if process and process.returncode is None:process.kill();await process.wait()
            server.close();await server.wait_closed()
            pending=tuple(tasks)
            for task in pending:task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)

    async def test_registration_pairing_rejection_and_native_tls_data(self):
        exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_relay_session_tests.exe'
        relay=Relay();server=await asyncio.start_server(relay.handle,'127.0.0.1',0,ssl=self.tls.server,limit=2048)
        port=server.sockets[0].getsockname()[1];processes=[]
        async def launch(*args):
            process=await asyncio.create_subprocess_exec(str(exe),str(port),str(self.tls.der),*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            processes.append(process);return process
        try:
            host=await launch('host')
            line=await asyncio.wait_for(host.stdout.readline(),5)
            room,token=line.decode().strip().split()
            rejected=await launch('reject',room,'a'*43)
            out,error=await asyncio.wait_for(rejected.communicate(),5)
            self.assertEqual(rejected.returncode,0,error.decode(errors='replace'))
            self.assertIn('This online game is no longer available.',error.decode())
            peer=await launch('peer',room,token)
            for process in [host,peer]:
                out,error=await asyncio.wait_for(process.communicate(),10)
                self.assertEqual(process.returncode,0,error.decode(errors='replace'))
            for _ in range(100):
                if not relay.rooms:break
                await asyncio.sleep(.01)
            self.assertFalse(relay.rooms)
        finally:
            for process in processes:
                if process.returncode is None:process.kill();await process.wait()
            server.close();await server.wait_closed();await relay.close()

if __name__=='__main__':unittest.main()
