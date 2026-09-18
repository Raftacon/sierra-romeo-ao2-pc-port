"""Ephemeral test-only certificate; never installs trust in the OS store."""
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path


class RelayTLSFixture:
    def __init__(self):
        self.folder=tempfile.TemporaryDirectory(prefix='aot-relay-tls-')
        root=Path(self.folder.name);cert=root/'server.pem';key=root/'server-key.pem'
        openssl=shutil.which('openssl')
        if not openssl:raise RuntimeError('OpenSSL required for relay certificate tests')
        subprocess.run([openssl,'req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-subj','/CN=Local Relay Test','-addext','subjectAltName=IP:127.0.0.1',
            '-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
        self.server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server.minimum_version=ssl.TLSVersion.TLSv1_2
        self.server.load_cert_chain(cert,key)
        self.client=ssl.create_default_context(cafile=str(cert))
        self.der=root/'server.der'
        self.der.write_bytes(ssl.PEM_cert_to_DER_cert(cert.read_text()))

    def close(self):self.folder.cleanup()
