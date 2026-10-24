from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from subprocess import CalledProcessError
from unittest.mock import MagicMock, Mock, patch

from agent.base import Base
from agent.builder import PatchImageBuilder
from agent.exceptions import AgentException
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

    def test_server_login_forwards_stdin_through_real_execute(self):
        server = Server.__new__(Server)
        Base.__init__(server)
        password = "synthetic-server-stdin-canary"
        server.run_subprocess = Mock(return_value=("Login Succeeded", 0))

        with patch("builtins.print"), patch.object(Server, "update_redis"):
            server.docker_login({"url": "registry.example.test", "username": "user", "password": password})

        self.assertTrue(server.run_subprocess.call_args.args[2] == password)
        self.assertTrue(password not in str(server.data))

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

    def test_patch_image_builder_login_uses_standard_input_without_password_in_argv(self):
        password = "fake-registry-pw"
        builder = PatchImageBuilder.__new__(PatchImageBuilder)
        builder.registry = {
            "url": "registry.example.test",
            "username": "registry-user",
            "password": password,
        }
        builder.base_image = "registry.example.test/base:latest"
        builder.container_name = "patch-build-test"
        builder.execute = Mock()

        PatchImageBuilder._start_base_container.__wrapped__(builder)

        login_command, login_kwargs = builder.execute.call_args_list[0]
        self.assertEqual(
            login_command[0],
            ["docker", "login", "--username", "registry-user", "--password-stdin", "registry.example.test"],
        )
        self.assertEqual(login_kwargs, {"input": password})
        self.assertNotIn(password, " ".join(login_command[0]))

    def test_failed_patch_image_login_does_not_store_password_in_execution_data(self):
        password = "fake-registry-pw"
        builder = PatchImageBuilder(
            "registry.example.test/base:latest",
            "registry.example.test/patches",
            "latest",
            False,
            {"url": "registry.example.test", "username": "registry-user", "password": password},
            [],
            "test",
        )
        builder.run_subprocess = Mock(
            side_effect=CalledProcessError(1, ["docker", "login", "--password-stdin"], output="login failed")
        )

        with patch("builtins.print") as printed, self.assertRaises(AgentException) as raised:
            PatchImageBuilder._start_base_container.__wrapped__(builder)

        self.assertNotIn(password, str(raised.exception.data))
        self.assertNotIn(password, builder.data["command"])
        self.assertNotIn(password, "\n".join(str(call) for call in printed.call_args_list))

    def test_run_subprocess_writes_an_empty_input_value(self):
        base = Base()
        process = MagicMock()
        process.poll.return_value = 0
        popen = MagicMock()
        popen.return_value.__enter__.return_value = process

        with patch("agent.base.subprocess.Popen", popen), patch.object(base, "parse_output", return_value=""):
            base.run_subprocess(["docker", "login"], None, "", None)

        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)
        process._stdin_write.assert_called_once_with(b"")
