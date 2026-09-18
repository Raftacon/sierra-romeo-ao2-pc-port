"""Explicit release check; never downloads or executes an installer automatically."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def https_url(value):
    if not isinstance(value, str):
        raise ValueError('Expected an HTTPS URL')
    url = urllib.parse.urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError('Release links must use HTTPS without credentials or fragments')
    return value


def validate_manifest(data):
    if not isinstance(data, dict) or data.get('kind') != 'source-only-installer':
        raise ValueError('Expected a source-only installer manifest')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?', str(data.get('version', ''))):
        raise ValueError('Invalid release version')
    for key in ('download', 'release_notes'):
        https_url(data.get(key))
    if not re.fullmatch('[a-f0-9]{64}', str(data.get('sha256', ''))):
        raise ValueError('Invalid source bundle checksum')
    return data


class HttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', help='Explicit trusted HTTPS release manifest; no default public service yet')
    args = parser.parse_args()
    version = json.loads((ROOT / 'release/version.json').read_text())
    address = args.manifest or version['update_manifest']
    if not address:
        print(f"Sierra Romeo {version['version']}: no update service configured. No network request made.")
        return
    request = urllib.request.Request(https_url(address), headers={'User-Agent': 'SierraRomeo-ReleaseCheck/1'})
    with urllib.request.build_opener(HttpsRedirect()).open(request, timeout=15) as response:
        payload = response.read(65537)
    if len(payload) > 65536:
        raise ValueError('Release manifest too large')
    release = validate_manifest(json.loads(payload))
    print(json.dumps({'installed': version['version'], 'offered': release,
                      'action': 'Review release notes. This command does not install updates.'}, indent=2))


if __name__ == '__main__':
    main()
