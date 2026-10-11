"""Shared pytest setup for the Innovation Radar test suite.

Two jobs. First, put the repo root on sys.path so `pipeline`, `llm` and `app`
import the same way from any test file instead of relying on whichever test
module happened to be collected first. Second, block outbound sockets for the
whole suite: every test is supposed to mock its feed and LLM calls, but that
was only ever checked by reading the code, so one forgotten monkeypatch used to
hit the real network -- slow, flaky and quota-burning. A test that forgets now
fails with a clear error instead.
"""
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


class NetworkAccessBlocked(RuntimeError):
    """Raised instead of opening a real connection from a test."""


def _blocked(*args, **kwargs):
    raise NetworkAccessBlocked(
        "This test tried to open a network connection. Tests must mock every "
        "HTTP/feed/LLM call -- monkeypatch the client (see the existing "
        "monkeypatch.setattr(ingest.feedparser, 'parse', ...) pattern)."
    )


# Set only while socket.socketpair() runs. Where the OS has no native
# socketpair (Windows), Python emulates it by connecting to a listener on
# 127.0.0.1, and asyncio -- which Streamlit's AppTest starts -- creates one
# for its wake-up pipe. That connection never leaves the process, so it is
# let through for exactly that call; every other loopback connection (a local
# Ollama, for one) stays refused like the rest of the network.
_in_socketpair = threading.local()


def _guard(original):
    """`original` for the connection socketpair makes of itself, `_blocked` otherwise."""

    def guarded(*args, **kwargs):
        if getattr(_in_socketpair, "active", False):
            return original(*args, **kwargs)
        return _blocked(*args, **kwargs)

    return guarded


def _socketpair_allowing_its_own_connect(original):
    def socketpair(*args, **kwargs):
        _in_socketpair.active = True
        try:
            return original(*args, **kwargs)
        finally:
            _in_socketpair.active = False

    return socketpair


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Make any attempt to reach the network raise.

    Patches the connect entry points rather than socket.socket itself, so
    creating a socket object, resolving nothing and anything local (SQLite runs
    on files and :memory:, never on a socket) keeps working -- only an actual
    connection is refused.
    """
    monkeypatch.setattr(socket.socket, "connect", _guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "create_connection", _guard(socket.create_connection))
    monkeypatch.setattr(socket, "socketpair", _socketpair_allowing_its_own_connect(socket.socketpair))
