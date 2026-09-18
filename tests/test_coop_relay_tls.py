"""TLS trust/hostname rejection without changing machine certificate stores."""
import asyncio
import ssl
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from coop_rendezvous import Relay
from coop_relay_bridge import RelayBridge
from relay_tls_fixture import RelayTLSFixture


class RelayTLSTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):cls.tls=RelayTLSFixture()

    @classmethod
    def tearDownClass(cls):cls.tls.close()

    async def asyncSetUp(self):
        self.relay=Relay()
        self.server=await asyncio.start_server(self.relay.handle,'127.0.0.1',0,ssl=self.tls.server,limit=2048)
        self.port=self.server.sockets[0].getsockname()[1]
        self.bridges=[]
        self.loop=asyncio.get_running_loop()
        self.old_handler=self.loop.get_exception_handler()
        def expected_handshake_failure(loop,context):
            if isinstance(context.get('exception'),(ssl.SSLError,ConnectionResetError)):return
            if self.old_handler:self.old_handler(loop,context)
            else:loop.default_exception_handler(context)
        self.loop.set_exception_handler(expected_handshake_failure)

    async def asyncTearDown(self):
        for bridge in self.bridges:await bridge.close()
        self.server.close();await self.server.wait_closed();await self.relay.close()
        self.loop.set_exception_handler(self.old_handler)

    def bridge(self,host='127.0.0.1',context=None):
        bridge=RelayBridge(host,self.port,ssl_context=context)
        self.bridges.append(bridge);return bridge

    async def test_explicit_test_ca_and_matching_ip(self):
        bridge=self.bridge(context=self.tls.client)
        registration=await bridge.host(37001)
        self.assertIn(registration['room'],self.relay.rooms)

    async def test_untrusted_certificate_rejected_before_registration(self):
        with self.assertRaises(ssl.SSLCertVerificationError):await self.bridge().connect()
        self.assertFalse(self.relay.rooms)

    async def test_wrong_hostname_rejected_even_with_trusted_ca(self):
        with self.assertRaises(ssl.SSLCertVerificationError):await self.bridge('localhost',self.tls.client).connect()
        self.assertFalse(self.relay.rooms)

    async def test_remote_plaintext_is_refused(self):
        with self.assertRaises(ValueError):RelayBridge('192.0.2.1',37004,tls=False)

if __name__=='__main__':unittest.main()
