"""Native bridge workers carry actual private/public CoopLobby traffic over TLS."""
import asyncio
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from coop_rendezvous import Relay
from relay_tls_fixture import RelayTLSFixture


class NativeBridgeTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):cls.tls=RelayTLSFixture()
    @classmethod
    def tearDownClass(cls):cls.tls.close()

    async def asyncSetUp(self):
        self.relay=Relay()
        self.server=await asyncio.start_server(self.relay.handle,'127.0.0.1',0,limit=2048,ssl=self.tls.server)
        self.port=self.server.sockets[0].getsockname()[1]
        self.processes=[]
        self.build=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo'

    async def asyncTearDown(self):
        for process in self.processes:
            if process.returncode is None:process.kill();await process.wait()
        self.server.close();await self.server.wait_closed();await self.relay.close()

    async def start(self,exe,*args):
        process=await asyncio.create_subprocess_exec(str(self.build/exe),*map(str,args),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        self.processes.append(process)
        return process

    async def line(self,process):
        line=await asyncio.wait_for(process.stdout.readline(),8)
        self.assertTrue(line,await process.stderr.read() if process.returncode is not None else 'no bridge announcement')
        return line.decode().strip().split()

    async def success(self,process):
        _,error=await asyncio.wait_for(process.communicate(),20)
        self.assertEqual(process.returncode,0,error.decode(errors='replace'))

    async def test_cancel_waiting_host(self):
        bridge=await self.start('aot_coop_relay_bridge_tests.exe',self.port,self.tls.der,'cancel',12345)
        await self.line(bridge)
        await asyncio.wait_for(self.success(bridge),8)

    async def test_expired_host_registers_fresh_invitation(self):
        self.relay.wait_seconds=.2
        bridge=await self.start('aot_coop_relay_bridge_tests.exe',self.port,self.tls.der,'renew',12345)
        first=await self.line(bridge)
        second=await self.line(bridge)
        self.assertNotEqual(first[0],second[0])
        self.assertNotEqual(first[1],second[1])
        await self.success(bridge)

    async def test_private_and_public_native_transport(self):
        await self.native_transport('peer')

    async def test_rejected_peer_retries_with_valid_invitation(self):
        await self.native_transport('retry-peer')

    async def native_transport(self,peer_mode):
        for visibility in [1,0]:
            with self.subTest(visibility=visibility):
                host=await self.start('aot_coop_lobby_tests.exe','--relay-host',visibility)
                game_port,invite=await self.line(host)
                host_bridge=await self.start('aot_coop_relay_bridge_tests.exe',self.port,self.tls.der,'host',game_port)
                room,token=await self.line(host_bridge)
                peer_bridge=await self.start('aot_coop_relay_bridge_tests.exe',self.port,self.tls.der,peer_mode,room,token)
                peer_port,=await self.line(peer_bridge)
                peer=await self.start('aot_coop_lobby_tests.exe','--peer',peer_port,visibility,invite)
                for process in [host,peer,host_bridge,peer_bridge]:await self.success(process)

if __name__=='__main__':unittest.main()
