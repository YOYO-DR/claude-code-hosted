"""Supervisor: sólo acciones permitidas y armado correcto del comando."""

import subprocess

import pytest

from workers import supervisor


def test_rejects_unknown_action():
    with pytest.raises(ValueError):
        supervisor._run("restart", "abc")  # noqa: SLF001


def test_start_builds_expected_unit(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    supervisor.start("sid-123")
    assert "start" in captured["cmd"]
    assert "claude-session@sid-123.service" in captured["cmd"]


def test_start_raises_on_failure(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(supervisor.SupervisorError):
        supervisor.start("sid-123")


def test_is_active_reads_returncode(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")
    )
    assert supervisor.is_active("sid-123") is True
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, "", "")
    )
    assert supervisor.is_active("sid-123") is False
