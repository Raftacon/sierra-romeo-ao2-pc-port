"""Owned relay bridge for integration tests; not yet embedded in the game UI.

The game's existing TCP codec passes through unchanged. Local endpoints bind
only loopback. Remote relays require normal certificate-validated TLS.
"""
import asyncio
import contextlib
import re
import ssl
from coop_rendezvous import Relay, header, message


class RelayBridge:
    def __init__(self, relay_host, relay_port, tls=True, ssl_context=None):
        if not tls and relay_host not in ('127.0.0.1','::1','localhost'):
            raise ValueError('Remote relay connections require TLS')
        self.relay_host,self.relay_port=relay_host,relay_port
        self.context=(ssl_context or ssl.create_default_context()) if tls else None
        self.server=None
        self.tasks=set()
        self.writers=set()
        self.claimed=False
        self.failure=None

    async def connect(self):
        reader,writer=await asyncio.wait_for(asyncio.open_connection(
            self.relay_host,self.relay_port,ssl=self.context,limit=2048),5)
        self.writers.add(writer)
        return reader,writer

    def start(self,coroutine):
        task=asyncio.create_task(coroutine);self.tasks.add(task)
        def finished(value):
            self.tasks.discard(value)
            if not value.cancelled():
                error=value.exception()
                if error:self.failure=type(error).__name__  # Never retain credentials/payloads.
        task.add_done_callback(finished)
        return task

    async def close_writer(self,writer):
        writer.close()
        with contextlib.suppress(ConnectionError,OSError):await writer.wait_closed()
        self.writers.discard(writer)

    async def host(self,game_port):
        if self.claimed:raise RuntimeError('Bridge already in use')
        self.claimed=True
        reader,writer=await self.connect()
        try:
            await message(writer,{'version':1,'role':'host'})
            registration=await header(reader)
            if not re.fullmatch('[0-9a-f]{32}',registration.get('room','')) or not re.fullmatch('[A-Za-z0-9_-]{43}',registration.get('join_token','')):
                raise ValueError('Invalid relay registration')
            self.start(self.host_stream(reader,writer,game_port))
            return {'room':registration['room'],'join_token':registration['join_token']}
        except BaseException:
            await self.close_writer(writer)
            raise

    async def host_stream(self,reader,writer,game_port):
        local_writer=None
        try:
            if await header(reader,125)!={'version':1,'paired':True}:raise ValueError('Relay did not pair host')
            local_reader,local_writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',game_port),5)
            self.writers.add(local_writer)
            await Relay().tunnel(local_reader,local_writer,reader,writer)
        finally:
            await self.close_writer(writer)
            if local_writer:await self.close_writer(local_writer)

    async def peer(self,registration):
        if self.server or self.claimed:raise RuntimeError('Bridge already in use')
        async def handle(local_reader,local_writer):
            if self.claimed:
                await self.close_writer(local_writer)
                return
            self.claimed=True;self.writers.add(local_writer)
            writer=None
            try:
                reader,writer=await self.connect()
                await message(writer,{'version':1,'role':'peer',**registration})
                if await header(reader)!={'version':1,'paired':True}:raise ValueError('Relay did not pair peer')
                await Relay().tunnel(local_reader,local_writer,reader,writer)
            finally:
                await self.close_writer(local_writer)
                if writer:await self.close_writer(writer)
        self.server=await asyncio.start_server(lambda r,w:self.start(handle(r,w)),'127.0.0.1',0)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        if self.server:
            self.server.close();await self.server.wait_closed()
        tasks=tuple(self.tasks)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for writer in tuple(self.writers):await self.close_writer(writer)
