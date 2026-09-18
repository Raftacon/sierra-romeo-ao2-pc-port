"""Real TCP tests for outbound/outbound pairing and opaque byte forwarding."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from coop_rendezvous import Relay, message


class RelayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.relay=Relay(wait_seconds=2,idle_seconds=2)
        self.server=await asyncio.start_server(self.relay.handle,'127.0.0.1',0,limit=2048)
        self.port=self.server.sockets[0].getsockname()[1]
        self.writers=[]

    async def asyncTearDown(self):
        for writer in self.writers:writer.close()
        for writer in self.writers:
            try:await writer.wait_closed()
            except ConnectionError:pass
        self.server.close();await self.server.wait_closed();await self.relay.close()

    async def connect(self,request):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        self.writers.append(writer);await message(writer,request)
        return reader,writer

    async def response(self,reader):
        return json.loads(await asyncio.wait_for(reader.readline(),2))

    async def host(self):
        reader,writer=await self.connect({'version':1,'role':'host'})
        registration=await self.response(reader)
        self.assertEqual(len(registration['room']),32)
        self.assertEqual(len(registration['join_token']),43)
        return reader,writer,registration

    async def pair(self):
        hr,hw,room=await self.host()
        pr,pw=await self.connect({'version':1,'role':'peer',**{k:room[k] for k in ('room','join_token')}})
        self.assertTrue((await self.response(hr))['paired'])
        self.assertTrue((await self.response(pr))['paired'])
        return hr,hw,pr,pw,room

    async def test_binary_duplex_and_half_close(self):
        hr,hw,pr,pw,_=await self.pair()
        left=bytes(range(256))*4096;right=bytes(reversed(range(256)))*2048
        async def send(writer,payload):
            for offset in range(0,len(payload),997):
                writer.write(payload[offset:offset+997]);await writer.drain()
            writer.write_eof()
        _,_,received_left,received_right=await asyncio.wait_for(asyncio.gather(
            send(hw,left),send(pw,right),pr.read(),hr.read()),5)
        self.assertEqual(received_left,left);self.assertEqual(received_right,right)

    async def test_wrong_token_does_not_claim_room(self):
        hr,hw,room=await self.host()
        rejected,_=await self.connect({'version':1,'role':'peer','room':room['room'],'join_token':'wrong'})
        self.assertEqual((await self.response(rejected))['error'],'room unavailable')
        pr,_=await self.connect({'version':1,'role':'peer','room':room['room'],'join_token':room['join_token']})
        self.assertTrue((await self.response(hr))['paired']);self.assertTrue((await self.response(pr))['paired'])

    async def test_duplicate_peer_cannot_take_active_room(self):
        hr,hw,pr,pw,room=await self.pair()
        rejected,_=await self.connect({'version':1,'role':'peer','room':room['room'],'join_token':room['join_token']})
        self.assertIn('error',await self.response(rejected))
        hw.write(b'original peer');await hw.drain()
        self.assertEqual(await asyncio.wait_for(pr.readexactly(13),2),b'original peer')

    async def test_room_isolation(self):
        first=await self.pair();second=await self.pair()
        for pair,data in [(first,b'first'),(second,b'second')]:pair[1].write(data);await pair[1].drain()
        self.assertEqual(await first[2].readexactly(5),b'first')
        self.assertEqual(await second[2].readexactly(6),b'second')

    async def test_waiting_host_disconnect_removes_room(self):
        _,writer,room=await self.host();writer.close();await writer.wait_closed()
        for _ in range(100):
            if room['room'] not in self.relay.rooms:break
            await asyncio.sleep(.01)
        self.assertNotIn(room['room'],self.relay.rooms)

    async def test_room_capacity_and_wait_expiry(self):
        self.relay.max_rooms=1;self.relay.wait_seconds=.1
        hr,_,room=await self.host()
        denied,_=await self.connect({'version':1,'role':'host'})
        self.assertIn('error',await self.response(denied))
        self.assertEqual(await asyncio.wait_for(hr.read(),1),b'')
        self.assertNotIn(room['room'],self.relay.rooms)

    async def test_invalid_handshake_closes(self):
        reader,_=await self.connect({'version':2,'role':'host'})
        self.assertEqual(await asyncio.wait_for(reader.read(),1),b'')
        self.assertEqual(len(self.relay.rooms),0)

    async def test_idle_pair_is_retired(self):
        self.relay.idle_seconds=.1
        hr,_,pr,_,room=await self.pair()
        self.assertEqual(await asyncio.wait_for(hr.read(),1),b'')
        self.assertEqual(await asyncio.wait_for(pr.read(),1),b'')
        self.assertNotIn(room['room'],self.relay.rooms)


if __name__=='__main__':unittest.main()
