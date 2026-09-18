"""Development co-op relay: both host and peer connect outbound to this server.

No game packets are decoded or logged. Separate from the delay-test proxy.
Loopback by default; this is not a deployed or production-hardened service.
"""
import argparse
import asyncio
import contextlib
import hmac
import json
import re
import secrets
import ssl
from dataclasses import dataclass


async def message(writer, value):
    writer.write(json.dumps(value, separators=(',', ':')).encode()+b'\n')
    await writer.drain()


async def header(reader, timeout=5):
    line = await asyncio.wait_for(reader.readline(), timeout)
    if len(line)>1024 or not line.endswith(b'\n'):
        raise ValueError('invalid handshake')
    value = json.loads(line)
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('unsupported handshake')
    return value


@dataclass
class Room:
    token: str
    peer: asyncio.Future
    finished: asyncio.Future
    claimed: bool = False


class Relay:
    def __init__(self, max_rooms=64, max_connections=256, wait_seconds=120, idle_seconds=45):
        self.rooms = {}
        self.max_rooms, self.max_connections = max_rooms, max_connections
        self.wait_seconds, self.idle_seconds = wait_seconds, idle_seconds
        self.tasks = set()
        self.active = 0

    async def close(self):
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def pump(self, reader, writer):
        while True:
            data = await asyncio.wait_for(reader.read(32768), self.idle_seconds)
            if not data:
                if writer.can_write_eof():
                    writer.write_eof()
                    await writer.drain()
                else:
                    # TLS streams cannot half-close. End the TLS connection so
                    # the remote bridge observes the game socket's disconnect.
                    # Previously this waited for the idle timeout instead.
                    writer.close()
                    await writer.wait_closed()
                return
            writer.write(data)
            await asyncio.wait_for(writer.drain(), self.idle_seconds)

    async def tunnel(self, host_reader, host_writer, peer_reader, peer_writer):
        streams = [asyncio.create_task(self.pump(host_reader, peer_writer)),
                   asyncio.create_task(self.pump(peer_reader, host_writer))]
        try:
            await asyncio.gather(*streams)
        finally:
            for stream in streams:
                stream.cancel()
            await asyncio.gather(*streams, return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        admitted = False
        room_id, room = None, None
        try:
            if self.active >= self.max_connections:
                await message(writer, {'error':'relay busy'})
                return
            self.active += 1
            admitted = True
            request = await header(reader)
            if request == {'version':1, 'role':'host'}:
                if len(self.rooms) >= self.max_rooms:
                    await message(writer, {'error':'room limit reached'})
                    return
                loop = asyncio.get_running_loop()
                room_id = secrets.token_hex(16)
                room = Room(secrets.token_urlsafe(32), loop.create_future(), loop.create_future())
                self.rooms[room_id] = room
                await message(writer, {'version':1,'room':room_id,'join_token':room.token})
                # The host waits for the pairing response before sending game bytes.
                # Detect disconnected waiting hosts immediately, without retaining
                # stale rooms until the admission deadline.
                disconnected = asyncio.create_task(reader.read(1))
                try:
                    done, _ = await asyncio.wait([room.peer, disconnected], timeout=self.wait_seconds,
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if room.peer not in done or disconnected in done:
                        return
                    peer_reader, peer_writer = room.peer.result()
                finally:
                    disconnected.cancel()
                    await asyncio.gather(disconnected, return_exceptions=True)
                await message(writer, {'version':1,'paired':True})
                await message(peer_writer, {'version':1,'paired':True})
                await self.tunnel(reader, writer, peer_reader, peer_writer)
            elif set(request) == {'version','role','room','join_token'} and request['role']=='peer':
                if not isinstance(request['room'],str) or not isinstance(request['join_token'],str):
                    raise ValueError('invalid credentials')
                if not re.fullmatch(r'[0-9a-f]{32}',request['room']) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',request['join_token']):
                    await message(writer, {'error':'room unavailable'})
                    return
                target = self.rooms.get(request['room'])
                if not target or target.claimed or not hmac.compare_digest(target.token, request['join_token']):
                    await message(writer, {'error':'room unavailable'})
                    return
                target.claimed = True  # Reserve before any await; only one peer.
                target.peer.set_result((reader, writer))
                await asyncio.shield(target.finished)
            else:
                raise ValueError('invalid role')
        except (ValueError, UnicodeError, asyncio.TimeoutError, ConnectionError, OSError):
            # No peer-controlled data, session tokens or gameplay payload logging.
            pass
        finally:
            if room_id is not None:
                self.rooms.pop(room_id, None)
                if room and not room.finished.done():
                    room.finished.set_result(None)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()
            if admitted:
                self.active -= 1
            self.tasks.discard(task)


async def serve(args):
    context = None
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
    relay = Relay()
    server = await asyncio.start_server(relay.handle, args.bind, args.port, limit=2048, ssl=context)
    print(f'Development co-op relay listening on {args.bind}:{args.port}', flush=True)
    try:
        async with server:
            await server.serve_forever()
    finally:
        await relay.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=37004)
    parser.add_argument('--cert')
    parser.add_argument('--key')
    args = parser.parse_args()
    if not 1<=args.port<=65535 or bool(args.cert)!=bool(args.key):
        parser.error('Use a valid port and both certificate/key paths when enabling TLS')
    if args.bind not in ('127.0.0.1','::1','localhost') and not args.cert:
        parser.error('Non-loopback listeners require a TLS certificate and key')
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__=='__main__':
    main()
