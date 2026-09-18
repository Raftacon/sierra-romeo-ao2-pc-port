"""Actual native CoopLobby processes through two outbound bridges and the relay."""
import asyncio
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from coop_rendezvous import Relay
from coop_relay_bridge import RelayBridge
from relay_tls_fixture import RelayTLSFixture


class NativeRelayTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):cls.tls=RelayTLSFixture()

    @classmethod
    def tearDownClass(cls):cls.tls.close()

    async def test_private_and_public_transport(self):
        await self.transport(False)

    async def test_private_and_public_tls_transport(self):
        await self.transport(True)

    async def transport(self,encrypted):
        exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_lobby_tests.exe'
        self.assertTrue(exe.exists())
        for visibility in [1,0]:
            with self.subTest(visibility=visibility):
                relay=Relay();server=await asyncio.start_server(relay.handle,'127.0.0.1',0,limit=2048,ssl=self.tls.server if encrypted else None)
                port=server.sockets[0].getsockname()[1]
                host_bridge=RelayBridge('127.0.0.1',port,tls=encrypted,ssl_context=self.tls.client)
                peer_bridge=RelayBridge('127.0.0.1',port,tls=encrypted,ssl_context=self.tls.client)
                processes=[]
                try:
                    host=await asyncio.create_subprocess_exec(str(exe),'--relay-host',str(visibility),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    processes.append(host)
                    line=await asyncio.wait_for(host.stdout.readline(),5)
                    game_port,invite=line.decode().strip().split()
                    registration=await host_bridge.host(int(game_port))
                    peer_port=await peer_bridge.peer(registration)
                    peer=await asyncio.create_subprocess_exec(str(exe),'--peer',str(peer_port),str(visibility),invite,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    processes.append(peer)
                    for process in processes:
                        out,error=await asyncio.wait_for(process.communicate(),15)
                        self.assertEqual(process.returncode,0,error.decode(errors='replace'))
                    self.assertIsNone(host_bridge.failure)
                    self.assertIsNone(peer_bridge.failure)
                finally:
                    for process in processes:
                        if process.returncode is None:process.kill();await process.wait()
                    await host_bridge.close();await peer_bridge.close()
                    server.close();await server.wait_closed();await relay.close()

if __name__=='__main__':unittest.main()
