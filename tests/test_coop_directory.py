"""Exercise directory leases/search over actual loopback HTTP connections."""
import concurrent.futures
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from coop_directory import Directory, Server


class DirectoryTest(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.directory = Directory(clock=lambda: self.now)
        self.server = Server(('127.0.0.1', 0), self.directory)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.settings = dict(name='PC Host', map='05_03', difficulty=0, port=37001,
                             protocol=11, players=1, joinable=True)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(5)

    def request(self, method, path='/v1/rooms', data=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['Authorization'] = 'Bearer '+token
        try:
            connection.request(method, path, None if data is None else json.dumps(data), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def create(self, **changes):
        status, room = self.request('POST', data=self.settings | changes)
        self.assertEqual(status, 201)
        return room

    def search(self, filters='protocol=11'):
        status, result = self.request('GET', '/v1/rooms?'+filters)
        self.assertEqual(status, 200)
        return result['rooms']

    def test_listing_filters_and_no_secrets(self):
        room = self.create()
        listed = self.search()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0], self.settings | {'id': room['id'], 'address': '127.0.0.1'})
        self.assertNotIn(room['lease'], json.dumps(listed))
        for filters in ['protocol=12', 'protocol=11&map=01_100', 'protocol=11&difficulty=2']:
            self.assertEqual(self.search(filters), [])
        self.assertEqual(len(self.search('protocol=11&map=05_03&difficulty=0')), 1)

    def test_relay_requires_server_configuration_and_keeps_lease_private(self):
        route={'room':'a'*32,'join_token':'b'*43}
        self.assertEqual(self.request('POST',data=self.settings|{'relay':route})[0],400)
        self.directory.relay_endpoint='tls://relay.example.test:37004'
        room=self.create(relay=route)
        self.assertEqual(self.search(),[])  # Older clients must not mistake this for direct TCP.
        listing=self.search('protocol=11&relay=1')[0]
        self.assertEqual(listing['relay'],route|{'endpoint':self.directory.relay_endpoint})
        self.assertNotIn(room['lease'],json.dumps(listing))
        self.assertEqual(self.request('POST',data=self.settings|{'relay':route|{'endpoint':'tls://wrong.test:9'}})[0],400)
        for broken in [{'room':'wrong','join_token':'b'*43},{'room':'a'*32,'join_token':'bad'}]:
            self.assertEqual(self.request('POST',data=self.settings|{'relay':broken})[0],400)
        self.assertEqual(self.request('PUT','/v1/rooms/'+room['id'],self.settings,room['lease'])[0],200)
        self.assertNotIn('relay',self.search()[0])

    def test_relay_endpoint_policy(self):
        for endpoint in ['tcp://remote.test:37004','tls://user@relay.test:37004','tls://relay.test:0','tls://relay.test:37004/path','tls://relay..test:37004']:
            with self.assertRaises(ValueError):Directory(relay_endpoint=endpoint)

    def test_lease_refresh_expiry_and_relisting(self):
        room = self.create(); path = '/v1/rooms/'+room['id']
        self.now += 40
        self.assertEqual(self.request('PUT', path, self.settings, room['lease'])[0], 200)
        self.now += 40
        self.assertEqual(len(self.search()), 1)
        self.now += 5
        self.assertEqual(self.search(), [])
        self.assertEqual(self.request('PUT', path, self.settings, room['lease'])[0], 404)
        self.assertNotEqual(self.create()['id'], room['id'])

    def test_full_and_started_rooms_hidden_then_reopen(self):
        room = self.create(); path = '/v1/rooms/'+room['id']
        for changes in [{'players': 2}, {'joinable': False}, {}]:
            self.assertEqual(self.request('PUT', path, self.settings | changes, room['lease'])[0], 200)
            self.assertEqual(len(self.search()), 0 if changes else 1)

    def test_only_owner_can_refresh_or_delete(self):
        room = self.create(); path = '/v1/rooms/'+room['id']
        other = self.create(name='Another Host')
        for token in [None, 'a'*43, other['lease']]:
            for method in ['PUT', 'DELETE']:
                self.assertEqual(self.request(method, path, self.settings, token)[0], 404)
        self.assertEqual(len(self.search()), 2)
        self.assertEqual(self.request('DELETE', path, token=room['lease'])[0], 200)
        self.assertEqual([x['id'] for x in self.search()], [other['id']])

    def test_bad_requests_do_not_create_rooms(self):
        for changes in [{'address': '1.2.3.4'}, {'name': 'Bad\nname'}, {'map': '../travel'},
                        {'port': True}, {'port': 65536}, {'protocol': 0}, {'difficulty': 3},
                        {'players': 0}, {'joinable': 1}]:
            self.assertEqual(self.request('POST', data=self.settings | changes)[0], 400)
        for filters in ['', 'protocol=11&protocol=11', 'protocol=11&unknown=x',
                        'protocol=11&map=', 'protocol=-1', 'protocol=11&difficulty=9']:
            self.assertEqual(self.request('GET', '/v1/rooms?'+filters)[0], 400)
        self.assertEqual(self.search(), [])

    def test_parallel_capacity_is_enforced_and_recovers(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.request('POST', data=self.settings), range(8)))
        self.assertEqual(sum(status == 201 for status, _ in responses), 4)
        self.assertEqual(sum(status == 429 for status, _ in responses), 4)
        self.assertEqual(len(self.search()), 4)
        self.now += 45
        self.create()
        self.assertEqual(len(self.search()), 1)


if __name__ == '__main__':
    unittest.main()
