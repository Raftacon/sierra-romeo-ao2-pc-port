"""Native Windows chain and hostname validation with ephemeral memory-only trust."""
import subprocess
import unittest
from pathlib import Path
from relay_tls_fixture import RelayTLSFixture

class NativeCertificateTest(unittest.TestCase):
    def test_windows_trust_and_hostname_policy(self):
        fixture=RelayTLSFixture()
        try:
            exe=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_coop_relay_certificate_tests.exe'
            result=subprocess.run([str(exe),str(fixture.der)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        finally:fixture.close()

if __name__=='__main__':unittest.main()
