"""Installation must retain recovery until boot passes and reject stale evidence."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import install_device


class InstallationTest(unittest.TestCase):
    def exercise(self, failure=None, gate_failure=False):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            for name in ('device-test-all.json', 'device-benchmark-report.json'):
                (build / name).write_text('{}')
            device = MagicMock()
            device.run.return_value = b'no'
            def boot(*args):
                # No disarming/removing the guard before the real reboot gate.
                self.assertFalse(any('rm -f ' + install_device.ROOT + '/armed' in str(c)
                                     for c in device.run.call_args_list))
                if failure:
                    raise RuntimeError(failure)
                return {'status': 'PASS'}
            with patch.object(sys, 'argv', ['install', '--host', 'ont', '--known-hosts', 'keys',
                                           '--build', directory, '--router-host', 'router']), \
                 patch.object(install_device, 'verify_build', return_value={'extension_sha256': 'x'}), \
                 patch.object(install_device, 'file_hash', return_value='hash'), \
                 patch.object(install_device, 'validate_reports', return_value={'status': 'PASS'}, side_effect=ValueError('stale') if gate_failure else None), \
                 patch.object(install_device, 'Device', return_value=device) as connection, \
                 patch.object(install_device, 'router_health', return_value={'status': 'PASS'}), \
                 patch.object(install_device, 'stage'), patch.object(install_device, 'activate'), \
                 patch.object(install_device, 'wait_healthy', return_value={'status': 'PASS'}), \
                 patch.object(install_device, 'reboot', side_effect=boot), \
                 patch.object(install_device, 'restore', return_value={'status': 'PASS'}) as restore:
                if gate_failure:
                    with self.assertRaisesRegex(ValueError, 'stale'):
                        install_device.main()
                    connection.assert_not_called()
                    return
                if failure:
                    with self.assertRaisesRegex(RuntimeError, failure):
                        install_device.main()
                    restore.assert_called_once()
                else:
                    install_device.main()
                    restore.assert_not_called()
                report = json.loads((build / 'device-install.json').read_text())
                self.assertEqual(report['status'], 'FAIL' if failure else 'PASS')
                device.close.assert_called_once()

    def test_install_commits_only_after_reboot(self):
        self.exercise()

    def test_failed_reboot_restores_stock(self):
        self.exercise(failure='boot failed')

    def test_stale_validation_prevents_connection(self):
        self.exercise(gate_failure=True)


if __name__ == '__main__':
    unittest.main()
