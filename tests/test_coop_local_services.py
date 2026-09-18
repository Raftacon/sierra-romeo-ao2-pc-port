"""Combined local services, native TLS traffic, and owned shutdown."""
import asyncio
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from coop_local_services import play_script, ps_literal


class LocalServicesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='aot-local-services-test-')
        self.folder = Path(self.temporary.name) / 'session'
        self.processes = []

    async def asyncTearDown(self):
        if self.folder.exists():
            (self.folder / 'stop').touch()
        for process in self.processes:
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        self.temporary.cleanup()

    async def launch(self, *command):
        process = await asyncio.create_subprocess_exec(*map(str, command), cwd=ROOT,
                                                      stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.processes.append(process)
        return process

    async def test_native_tls_and_stop(self):
        service = await self.launch(sys.executable, ROOT / 'tools/coop_local_services.py', '--output', self.folder,
                                    '--directory-port', 0, '--relay-port', 0, '--seconds', 30)
        status = {}
        for _ in range(200):
            if (self.folder / 'status.json').exists():
                status = json.loads((self.folder / 'status.json').read_text())
                if status['state'] == 'running':
                    break
            if service.returncode is not None:
                self.fail((await service.communicate())[1].decode())
            await asyncio.sleep(.05)
        self.assertEqual(status.get('state'), 'running')
        def request(path):
            with urlopen(status['directory'] + path, timeout=3) as response:
                return json.load(response)
        self.assertIsInstance(await asyncio.to_thread(request, '/health'), dict)
        self.assertEqual((await asyncio.to_thread(request, '/v1/rooms?protocol=11&relay=1'))['rooms'], [])
        port = int(status['relay'].rsplit(':', 1)[1])
        exe = ROOT / 'out/build/RelWithDebInfo/aot_coop_relay_session_tests.exe'
        host = await self.launch(exe, port, status['certificate'], 'host')
        room, token = (await asyncio.wait_for(host.stdout.readline(), 5)).decode().strip().split()
        peer = await self.launch(exe, port, status['certificate'], 'peer', room, token)
        for process in (host, peer):
            _, error = await asyncio.wait_for(process.communicate(), 10)
            self.assertEqual(process.returncode, 0, error.decode())
        script = (self.folder / 'play.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('-Controller', script)
        self.assertIn('finally', script)
        self.assertNotIn(token, script)
        (self.folder / 'stop').touch()
        _, error = await asyncio.wait_for(service.communicate(), 5)
        self.assertEqual(service.returncode, 0, error.decode())
        self.assertEqual(json.loads((self.folder / 'status.json').read_text())['state'], 'stopped')
        self.assertFalse(Path(status['certificate']).exists())
        for endpoint in (status['directory'], status['relay']):
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', int(endpoint.rsplit(':', 1)[1])), timeout=.5)

    async def test_directory_bind_failure_exits_cleanly(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            service = await self.launch(sys.executable, ROOT / 'tools/coop_local_services.py', '--output', self.folder,
                                        '--directory-port', occupied.getsockname()[1], '--relay-port', 0)
            _, error = await asyncio.wait_for(service.communicate(), 10)
            self.assertEqual(service.returncode, 1)
            self.assertIn(b'Local services could not start', error)
            self.assertEqual(json.loads((self.folder / 'status.json').read_text())['state'], 'failed')
            self.assertFalse((self.folder / 'play.ps1').exists())

    def test_powershell_paths_are_literal(self):
        self.assertEqual(ps_literal("C:/a'b/$x`test"), "'C:/a''b/$x`test'")

    async def test_play_shortcut_restores_environment_and_exit_code(self):
        game_root = self.folder / "game's $copy"
        (game_root / 'tools').mkdir(parents=True)
        certificate = self.folder / 'test.der'
        certificate.touch()
        observed = self.folder / 'observed.json'
        (game_root / 'tools/run.ps1').write_text(
            "param([switch]$Controller)\n"
            "@{controller=$Controller.IsPresent;directory=$env:AOT_COOP_DIRECTORY_URL;relay=$env:AOT_COOP_RELAY_ENDPOINT;certificate=$env:AOT_COOP_RELAY_TEST_CA} | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath "
            + ps_literal(observed) + "\nexit 7\n", encoding='utf-8-sig')
        status = {'directory': 'http://127.0.0.1:37002', 'relay': 'tls://127.0.0.1:37004', 'certificate': str(certificate)}
        shortcut = self.folder / 'play.ps1'
        shortcut.write_text(play_script(game_root, status), encoding='utf-8-sig')
        command = "$env:AOT_COOP_DIRECTORY_URL='previous'; $env:AOT_COOP_RELAY_ENDPOINT=$null; $env:AOT_COOP_RELAY_TEST_CA=$null; & " + ps_literal(shortcut)
        command += "; if($LASTEXITCODE -ne 7 -or $env:AOT_COOP_DIRECTORY_URL -ne 'previous' -or $env:AOT_COOP_RELAY_ENDPOINT -or $env:AOT_COOP_RELAY_TEST_CA){exit 1}; exit 0"
        process = await self.launch('powershell', '-NoProfile', '-Command', command)
        _, error = await asyncio.wait_for(process.communicate(), 10)
        self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
        self.assertEqual(json.loads(observed.read_text(encoding='utf-8-sig')), dict(status, controller=True))


if __name__ == '__main__':
    unittest.main()
