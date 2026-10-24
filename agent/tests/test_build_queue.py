"""Pure queue-policy tests; no Agent config, Redis, jobs or third-party imports."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

MODULE = Path(__file__).parents[1] / 'build_queue.py'


class BuildQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('tested_build_queue', MODULE)
        cls.policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.policy)

    def test_node4_never_uses_unbounded_default_workers(self):
        with patch.object(self.policy.socket, 'gethostname', return_value='NODE4'):
            self.assertEqual(self.policy.remote_build_queue(), 'bounded-build')

    def test_other_hosts_keep_existing_queue(self):
        for name in ['NODE1', 'NODE2', 'NODE3', 'development']:
            with self.subTest(host=name), patch.object(self.policy.socket, 'gethostname', return_value=name):
                self.assertEqual(self.policy.remote_build_queue(), 'default')

    def test_identity_failure_does_not_fall_back_to_default(self):
        with patch.object(self.policy.socket, 'gethostname', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.policy.remote_build_queue()


if __name__ == '__main__':unittest.main()
