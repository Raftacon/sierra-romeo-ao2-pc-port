"""Local development public co-op directory. Not a gameplay relay or hosted service."""
import argparse
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class DirectoryError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def integer(value, low, high):
    return type(value) is int and low <= value <= high


class Directory:
    """Bounded ephemeral listings. Host leases never appear in search responses."""
    def __init__(self, clock=time.monotonic, ttl=45, capacity=256, per_address=4, relay_endpoint=None):
        self.clock, self.ttl = clock, ttl
        self.capacity, self.per_address = capacity, per_address
        self.rooms = {}
        self.lock = threading.Lock()
        self.relay_endpoint = relay_endpoint
        if relay_endpoint is not None:
            if not re.fullmatch(r'(tls|tcp)://[A-Za-z0-9.-]{1,253}:[0-9]{1,5}',relay_endpoint):
                raise ValueError('Invalid configured relay endpoint')
            parsed=urlsplit(relay_endpoint)
            if parsed.hostname.startswith('.') or parsed.hostname.endswith('.') or '..' in parsed.hostname:
                raise ValueError('Invalid relay hostname')
            if parsed.scheme not in ('tls','tcp') or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not parsed.port or not 1<=parsed.port<=65535:
                raise ValueError('Invalid configured relay endpoint')
            if parsed.scheme=='tcp' and parsed.hostname not in ('127.0.0.1','localhost'):
                raise ValueError('Remote relays require TLS')

    def expire(self):
        now = self.clock()
        self.rooms = {key: room for key, room in self.rooms.items() if room['expires'] > now}

    def settings(self,data):
        required={'name', 'map', 'difficulty', 'port', 'protocol', 'players', 'joinable'}
        if not isinstance(data, dict) or set(data)-{'relay'} != required:
            raise DirectoryError(400, 'Expected name, map, difficulty, port, protocol, players and joinable.')
        if not isinstance(data['name'], str) or not re.fullmatch(r'[A-Za-z0-9 _.-]{1,32}', data['name']):
            raise DirectoryError(400, 'Invalid host name.')
        if not isinstance(data['map'], str) or not re.fullmatch(r'[A-Za-z0-9_]{1,64}', data['map']):
            raise DirectoryError(400, 'Invalid campaign map.')
        for key, low, high in [('difficulty', 0, 2), ('port', 1, 65535), ('protocol', 1, 65535), ('players', 1, 2)]:
            if not integer(data[key], low, high):
                raise DirectoryError(400, 'Invalid '+key+'.')
        if type(data['joinable']) is not bool:
            raise DirectoryError(400, 'Invalid joinable flag.')
        result=dict(data)
        if 'relay' in data:
            relay=data['relay']
            if not self.relay_endpoint or not isinstance(relay,dict) or set(relay)!={'room','join_token'} or not isinstance(relay['room'],str) or not isinstance(relay['join_token'],str) or not re.fullmatch(r'[0-9a-f]{32}',relay['room']) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',relay['join_token']):
                raise DirectoryError(400,'Invalid or unavailable relay route.')
            result['relay']=dict(endpoint=self.relay_endpoint,**relay)
        return result

    @staticmethod
    def public(room_id, room):
        return dict(id=room_id, address=room['address'], **room['settings'])

    def create(self, address, data):
        settings = self.settings(data)
        # Derive the endpoint from the TCP peer, never a forwarded header or
        # arbitrary submitted address. This server makes no outbound requests.
        address = str(ipaddress.IPv4Address(address))
        with self.lock:
            self.expire()
            if len(self.rooms) >= self.capacity or sum(r['address'] == address for r in self.rooms.values()) >= self.per_address:
                raise DirectoryError(429, 'Room limit reached; close an existing room or wait for its lease to expire.')
            room_id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
            self.rooms[room_id] = dict(address=address, settings=settings, token=token, expires=self.clock()+self.ttl)
            return dict(id=room_id, lease=token, lease_seconds=self.ttl, heartbeat_seconds=15)

    def update(self, room_id, token, data, remove=False):
        settings = None if remove else self.settings(data)
        with self.lock:
            self.expire()
            room = self.rooms.get(room_id)
            if not room or not token or not hmac.compare_digest(room['token'], token):
                raise DirectoryError(404, 'Room or lease not found.')
            if remove:
                del self.rooms[room_id]
                return {'removed': True}
            room.update(settings=settings, expires=self.clock()+self.ttl)
            return {'lease_seconds': self.ttl}

    def search(self, protocol, campaign=None, difficulty=None, include_relay=False):
        with self.lock:
            self.expire()
            return {'rooms': [self.public(key, room) for key, room in self.rooms.items()
                if room['settings']['protocol'] == protocol and room['settings']['joinable']
                and (include_relay or 'relay' not in room['settings'])
                and room['settings']['players'] < 2
                and (campaign is None or room['settings']['map'] == campaign)
                and (difficulty is None or room['settings']['difficulty'] == difficulty)],
                'lease_seconds': self.ttl}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, directory=None):
        self.directory = directory if directory is not None else Directory()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'AOTDirectory/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        # Avoid storing invitation/lease material, URLs or remote addresses.
        pass

    def reply(self, status, body):
        data = json.dumps(body, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,4}', lengths[0]):
            raise DirectoryError(400, 'A single Content-Length is required.')
        size = int(lengths[0])
        if not 1 <= size <= 2048:
            raise DirectoryError(413, 'Request body must be 1..2048 bytes.')
        if self.headers.get_content_type() != 'application/json':
            raise DirectoryError(415, 'Use application/json.')
        data = self.rfile.read(size)
        if len(data) != size:
            raise DirectoryError(400, 'Incomplete body.')
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise DirectoryError(400, 'Invalid JSON.')

    def route(self):
        try:
            parsed = urlsplit(self.path)
            path, directory = parsed.path, self.server.directory
            if self.command == 'GET' and path == '/health' and not parsed.query:
                return self.reply(200, {'service': 'aot-coop-directory', 'api': 1})
            if self.command == 'GET' and path == '/v1/rooms':
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query)-{'protocol', 'map', 'difficulty', 'relay'} or any(len(v) != 1 for v in query.values()):
                    raise DirectoryError(400, 'Invalid search filters.')
                protocol = query.get('protocol', [''])[0]
                difficulty = query.get('difficulty', [None])[0]
                campaign = query.get('map', [None])[0]
                relay = query.get('relay', ['0'])[0]
                if relay not in ('0','1'):
                    raise DirectoryError(400, 'Invalid relay capability.')
                if not re.fullmatch(r'[0-9]{1,5}', protocol) or not 1 <= int(protocol) <= 65535:
                    raise DirectoryError(400, 'A compatible protocol version is required.')
                if difficulty is not None and difficulty not in ('0', '1', '2'):
                    raise DirectoryError(400, 'Invalid difficulty filter.')
                if campaign is not None and not re.fullmatch(r'[A-Za-z0-9_]{1,64}', campaign):
                    raise DirectoryError(400, 'Invalid map filter.')
                return self.reply(200, directory.search(int(protocol), campaign, None if difficulty is None else int(difficulty),relay=='1'))
            if parsed.query:
                raise DirectoryError(400, 'Unexpected query.')
            if self.command == 'POST' and path == '/v1/rooms':
                return self.reply(201, directory.create(self.client_address[0], self.body()))
            match = re.fullmatch(r'/v1/rooms/([0-9a-f]{32})', path)
            if match and self.command in ('PUT', 'DELETE'):
                auth = self.headers.get('Authorization', '')
                token = auth[7:] if auth.startswith('Bearer ') else ''
                if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
                    raise DirectoryError(404, 'Room or lease not found.')
                return self.reply(200, directory.update(match[1], token,
                    None if self.command == 'DELETE' else self.body(), self.command == 'DELETE'))
            raise DirectoryError(404, 'Endpoint not found.')
        except DirectoryError as error:
            self.reply(error.status, {'error': error.message})
        except (TimeoutError, ConnectionError):
            self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = route


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=37002)
    parser.add_argument('--relay-endpoint', help='Configured tls://host:port, or loopback tcp://host:port for tests')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be 1..65535')
    with Server((args.bind, args.port),Directory(relay_endpoint=args.relay_endpoint)) as server:
        print(f'Local co-op directory listening on {args.bind}:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
