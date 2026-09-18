"""Run the local co-op directory and TLS relay together (loopback only)."""
import argparse
import asyncio
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from coop_directory import Directory, Server
from coop_rendezvous import Relay


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def write_status(folder, value):
    temporary = folder / 'status.tmp'
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(folder / 'status.json')


def play_script(root, status):
    values = {
        'AOT_COOP_DIRECTORY_URL': status['directory'],
        'AOT_COOP_RELAY_ENDPOINT': status['relay'],
        'AOT_COOP_RELAY_TEST_CA': status['certificate'],
    }
    lines = ["$ErrorActionPreference = 'Stop'",
             f"if (-not (Test-Path -LiteralPath {ps_literal(status['certificate'])})) {{ throw 'Start a new local co-op service session first.' }}",
             '$previous = @{}', 'try {']
    for name, value in values.items():
        lines += [f"    $previous[{ps_literal(name)}] = [Environment]::GetEnvironmentVariable({ps_literal(name)}, 'Process')",
                  f"    [Environment]::SetEnvironmentVariable({ps_literal(name)}, {ps_literal(value)}, 'Process')"]
    lines += [f"    & {ps_literal(root / 'tools/run.ps1')} -Controller",
              '    $gameExit = $LASTEXITCODE', '} finally {',
              "    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }",
              '}', 'exit $gameExit']
    return '\n'.join(lines) + '\n'


async def serve(args):
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    status = {'state': 'starting'}
    write_status(folder, status)
    relay = Relay()
    listener = directory = worker = None
    try:
        openssl = shutil.which('openssl')
        if not openssl:
            raise RuntimeError('OpenSSL must be on PATH to create the temporary local certificate.')
        with tempfile.TemporaryDirectory(prefix='aot-local-coop-') as temporary:
            cert_folder = Path(temporary)
            cert, key, der = [cert_folder / name for name in ('server.pem', 'server-key.pem', 'server.der')]
            subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', '/CN=Local Co-op Development', '-addext', 'subjectAltName=IP:127.0.0.1',
                            '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
            der.write_bytes(ssl.PEM_cert_to_DER_cert(cert.read_text()))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert, key)
            try:
                listener = await asyncio.start_server(relay.handle, '127.0.0.1', args.relay_port, limit=2048, ssl=context)
                endpoint = f'tls://127.0.0.1:{listener.sockets[0].getsockname()[1]}'
                directory = Server(('127.0.0.1', args.directory_port), Directory(relay_endpoint=endpoint))
                worker = threading.Thread(target=directory.serve_forever, kwargs={'poll_interval': .1})
                worker.start()
                status = {'state': 'running', 'directory': f'http://127.0.0.1:{directory.server_port}',
                          'relay': endpoint, 'certificate': str(der)}
                (folder / 'play.ps1').write_text(play_script(Path(__file__).resolve().parents[1], status), encoding='utf-8-sig')
                write_status(folder, status)
                print(f"Local services ready. In another PowerShell window run:\n& {ps_literal(folder / 'play.ps1')}\n"
                      'Use the original Private/Public Co-op menus. Ctrl+C stops both services.', flush=True)
                deadline = asyncio.get_running_loop().time() + args.seconds if args.seconds else None
                while not (folder / 'stop').exists():
                    if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                        break
                    await asyncio.sleep(.1)
            finally:
                if directory:
                    if worker and worker.is_alive():
                        await asyncio.to_thread(directory.shutdown)
                        worker.join()
                    directory.server_close()
                if listener:
                    listener.close()
                    await listener.wait_closed()
                await relay.close()
    except Exception:
        status['state'] = 'failed'
        raise
    finally:
        if status['state'] != 'failed':
            status['state'] = 'stopped'
        write_status(folder, status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New session folder for status and play.ps1')
    parser.add_argument('--directory-port', type=int, default=37002)
    parser.add_argument('--relay-port', type=int, default=37004)
    parser.add_argument('--seconds', type=int, default=0, help='Optional automatic stop; zero runs until Ctrl+C')
    args = parser.parse_args()
    if not all(0 <= port <= 65535 for port in (args.directory_port, args.relay_port)) or args.seconds < 0:
        parser.error('Ports must be 0..65535 (zero chooses a free port); seconds must be nonnegative.')
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Local services could not start: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
