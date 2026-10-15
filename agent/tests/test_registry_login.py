from __future__ import annotations

import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent.base import Base
from agent.server import Server


class TestRegistryLogin(unittest.TestCase):
    def test_docker_login_passes_password_on_standard_input(self):
        server = Server.__new__(Server)
        server.execute = Mock()
        password = "registry-password-for-test-only"

        server.docker_login(
            {
                "url": "registry.example.test",
                "username": "registry-user",
                "password": password,
            }
        )

        server.execute.assert_called_once_with(
            ["docker", "login", "--username", "registry-user", "--password-stdin", "registry.example.test"],
            input=password,
        )

    def test_execute_does_not_log_standard_input(self):
        secret = "registry-password-for-test-only"
        base = Base()
        base.job_record = None

        with tempfile.TemporaryDirectory() as directory, patch("builtins.print") as printed:
            base.execute(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                directory=directory,
                input=secret,
            )

        rendered_records = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertNotIn(secret, rendered_records)
        self.assertNotIn(secret, base.data["command"])
