from __future__ import annotations

import posixpath
import shlex
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.quant_generator import QuantProject


class RemoteDeployError(RuntimeError):
    pass


@dataclass
class RemoteDeployConfig:
    host: str
    username: str
    password: str
    remote_dir: str = "stock_strategy_quant"
    port: int = 22
    run_after_install: bool = False
    python_cmd: str = "python3"


def resolve_remote_path(home: str, remote_dir: str) -> str:
    value = str(remote_dir or "stock_strategy_quant").strip()
    if value.startswith("~/"):
        value = home.rstrip("/") + "/" + value[2:]
    elif value == "~":
        value = home
    elif not value.startswith("/"):
        value = home.rstrip("/") + "/" + value
    return posixpath.normpath(value)


def _quote(value: str) -> str:
    return shlex.quote(str(value))


def _archive(project_path: Path, destination: Path) -> Path:
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted(project_path.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(project_path)
            if any(part in (".venv", "__pycache__", "output") for part in relative.parts):
                continue
            archive.add(path, arcname=str(relative))
    return destination


def _python_version(output: str) -> tuple[int, int] | None:
    for token in output.replace("\n", " ").split():
        parts = token.strip().split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
    return None


def deploy_project(
    project: QuantProject,
    config: RemoteDeployConfig,
    progress: Callable[[str], None] | None = None,
    cancel_event=None,
) -> dict[str, object]:
    progress = progress or (lambda _message: None)
    if not config.host.strip():
        raise ValueError("SSH 主机 IP 不能为空")
    if not config.username.strip():
        raise ValueError("SSH 用户名不能为空")
    if not config.password:
        raise ValueError("SSH 密码不能为空")
    try:
        import paramiko
    except Exception as exc:
        raise RemoteDeployError("paramiko 未安装，无法使用 SSH 部署") from exc

    archive_path: Path | None = None
    client = None
    sftp = None
    remote_path = ""
    try:
        archive_path = Path(tempfile.mkstemp(prefix="strategy_deploy_", suffix=".tar.gz")[1])
        _archive(project.path, archive_path)
        progress(f"本地打包完成: {archive_path.name} ({archive_path.stat().st_size / 1024:.0f} KB)")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        progress(f"连接 {config.username}@{config.host}:{config.port} ...")
        client.connect(
            hostname=config.host.strip(),
            port=int(config.port),
            username=config.username.strip(),
            password=config.password,
            timeout=20,
            banner_timeout=25,
            auth_timeout=30,
            look_for_keys=False,
            allow_agent=False,
            compress=True,
        )
        transport = client.get_transport()
        if transport is not None:
            key = transport.get_remote_server_key()
            try:
                fingerprint = key.get_fingerprint().hex()
            except Exception:
                fingerprint = "unknown"
            progress(f"SSH 已连接 · 主机指纹 {fingerprint}")
        sftp = client.open_sftp()
        home = sftp.normalize(".")
        remote_path = resolve_remote_path(home, config.remote_dir)
        progress(f"远程目录: {remote_path}")
        def self_exec(command: str) -> str:
            return _exec_command(client, command, progress, cancel_event)
        self_exec(f"mkdir -p {_quote(remote_path)}")
        remote_archive = posixpath.join(remote_path, ".strategy_deploy.tar.gz")
        progress("上传项目压缩包 ...")
        sftp.put(str(archive_path), remote_archive)
        self_exec(f"cd {_quote(remote_path)} && tar -xzf {_quote(remote_archive)} && rm -f {_quote(remote_archive)}")
        self_exec(f"chmod +x {_quote(posixpath.join(remote_path, 'run.sh'))}")
        progress("项目已解压到远程目录")

        version_text = self_exec(f"{_quote(config.python_cmd)} --version")
        version = _python_version(version_text)
        if version is None:
            raise RemoteDeployError(f"无法识别远程 Python 版本: {version_text.strip()}")
        progress(f"远程 Python {version[0]}.{version[1]}")
        if version < (3, 10):
            raise RemoteDeployError("AKQuant 需要 Python 3.10 或更高版本")

        self_exec(f"cd {_quote(remote_path)} && {_quote(config.python_cmd)} -m venv .venv")
        progress("远程虚拟环境已就绪")
        self_exec(f"cd {_quote(remote_path)} && .venv/bin/python -m pip install --upgrade pip")
        progress("安装 AKQuant 与项目依赖 ...")
        self_exec(
            f"cd {_quote(remote_path)} && .venv/bin/python -m pip install -r requirements.txt"
        )
        progress("依赖安装完成，执行项目自检 ...")
        self_exec(f"cd {_quote(remote_path)} && .venv/bin/python -m quant_app.backtest --check")
        ran_backtest = False
        if config.run_after_install:
            progress("运行一次 AKQuant 回测 ...")
            self_exec(f"cd {_quote(remote_path)} && .venv/bin/python -m quant_app.backtest --no-progress")
            ran_backtest = True
        progress("SSH 自动安装完成")
        return {
            "remote_path": remote_path,
            "python": f"{version[0]}.{version[1]}",
            "ran_backtest": ran_backtest,
            "project": project.path,
        }
    finally:
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if archive_path is not None:
            try:
                archive_path.unlink(missing_ok=True)
            except Exception:
                pass


def _exec_command(client, command: str, progress, cancel_event=None, max_lines: int = 400) -> str:
    if cancel_event is not None and cancel_event.is_set():
        raise RemoteDeployError("用户取消部署")
    stdin, stdout, stderr = client.exec_command(command, get_pty=True)
    lines: list[str] = []
    channel = stdout.channel
    try:
        for raw in iter(stdout.readline, ""):
            line = raw.rstrip("\r\n")
            if line:
                lines.append(line)
                if len(lines) > max_lines:
                    lines = lines[-max_lines:]
                progress(f"  {line}")
            if cancel_event is not None and cancel_event.is_set():
                channel.close()
                raise RemoteDeployError("用户取消部署")
        status = channel.recv_exit_status()
        if status != 0:
            tail = "\n".join(lines[-30:])
            raise RemoteDeployError(f"远程命令失败 (exit={status}): {command}\n{tail}")
        return "\n".join(lines)
    finally:
        try:
            stdout.close()
        except Exception:
            pass
