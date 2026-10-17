from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.quant_generator import QuantProject
from core.remote_deploy import RemoteDeployConfig, deploy_project, resolve_remote_path


class FakeKey:
    def get_fingerprint(self):
        return b"\x01\x02\x03"


class FakeTransport:
    def get_remote_server_key(self):
        return FakeKey()


class FakeChannel:
    def __init__(self):
        self.closed = False

    def recv_exit_status(self):
        return 0

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, lines):
        self.lines = list(lines)
        self.channel = FakeChannel()
        self.closed = False

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        return ""

    def close(self):
        self.closed = True


class FakeSFTP:
    def __init__(self):
        self.uploads = []
        self.closed = False

    def normalize(self, _path):
        return "/home/tester"

    def put(self, local, remote):
        self.uploads.append((local, remote))

    def close(self):
        self.closed = True


class FakeSSHClient:
    commands: list[str] = []
    password = ""

    def __init__(self):
        self.sftp = FakeSFTP()
        self.closed = False

    def set_missing_host_key_policy(self, _policy):
        pass

    def connect(self, **kwargs):
        FakeSSHClient.password = kwargs.get("password", "")

    def get_transport(self):
        return FakeTransport()

    def open_sftp(self):
        return self.sftp

    def exec_command(self, command, get_pty=True):
        FakeSSHClient.commands.append(command)
        lines = ["Python 3.11.8\n"] if "--version" in command else ["ok\n"]
        return object(), FakeStream(lines), FakeStream([])

    def close(self):
        self.closed = True


class FakePolicy:
    pass


class RemoteDeployTest(unittest.TestCase):
    def test_deploy_orchestration_with_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "project"
            project_dir.mkdir()
            (project_dir / "run.sh").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
            (project_dir / "requirements.txt").write_text("akquant==0.3.67\n", encoding="utf-8")
            project = QuantProject(
                path=project_dir,
                strategy_name="test",
                symbol="sz000001",
                start="2024-01-01",
                end="2024-12-31",
                files=[],
                output=None,
            )
            fake_paramiko = types.SimpleNamespace(SSHClient=FakeSSHClient, AutoAddPolicy=FakePolicy)
            messages: list[str] = []
            FakeSSHClient.commands = []
            with patch.dict(sys.modules, {"paramiko": fake_paramiko}):
                result = deploy_project(
                    project,
                    RemoteDeployConfig(
                        host="example.com",
                        username="root",
                        password="secret-password",
                        remote_dir="quant",
                        run_after_install=True,
                    ),
                    progress=messages.append,
                )
            self.assertEqual(result["remote_path"], "/home/tester/quant")
            self.assertTrue(result["ran_backtest"])
            command_text = "\n".join(FakeSSHClient.commands)
            self.assertIn("python3 -m venv .venv", command_text)
            self.assertIn("pip install -r requirements.txt", command_text)
            self.assertIn("quant_app.backtest --check", command_text)
            self.assertIn("quant_app.backtest --no-progress", command_text)
            self.assertNotIn("secret-password", "\n".join(messages))
            self.assertNotIn("secret-password", command_text)

    def test_remote_path(self):
        self.assertEqual(resolve_remote_path("/home/u", "x"), "/home/u/x")


if __name__ == "__main__":
    unittest.main()
