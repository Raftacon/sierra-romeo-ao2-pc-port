import unittest
from tools.check_updates import validate_manifest, https_url


class UpdateManifestTests(unittest.TestCase):
    def example(self):
        return {'kind': 'source-only-installer', 'version': '0.1.0-dev',
                'download': 'https://example.org/source.zip',
                'release_notes': 'https://example.org/notes', 'sha256': 'a' * 64}

    def test_valid(self):
        self.assertEqual(validate_manifest(self.example())['version'], '0.1.0-dev')

    def test_rejects_other_payload_types(self):
        for kind in ('game', None, 'executable'):
            data = self.example(); data['kind'] = kind
            with self.assertRaises(ValueError): validate_manifest(data)

    def test_rejects_unsafe_links(self):
        for url in ('http://example.org/a', 'file:///a', 'https://user:pass@example.org/a',
                    'https:///a', 'https://example.org/#token'):
            with self.subTest(url=url), self.assertRaises(ValueError): https_url(url)

    def test_rejects_invalid_digest_and_version(self):
        for key, value in [('sha256', 'bad'), ('version', '../../bad')]:
            data = self.example(); data[key] = value
            with self.assertRaises(ValueError): validate_manifest(data)
