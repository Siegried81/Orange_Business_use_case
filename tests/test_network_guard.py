"""The socket guard in conftest.py: everything refused except socketpair's own connect.

The Windows socketpair emulation is replayed here on every platform by handing
the guard a stand-in that connects to a loopback listener, the way CPython's
fallback does, so the one hole in the guard is tested where it is cut.
"""

import socket

import pytest

import conftest


def test_socketpair_still_works_under_the_guard():
    """asyncio (Streamlit's AppTest) needs this at start-up on every platform."""
    a, b = socket.socketpair()
    try:
        a.sendall(b"ping")
        assert b.recv(4) == b"ping"
    finally:
        a.close()
        b.close()


def test_a_loopback_connection_outside_socketpair_is_refused():
    """A local service (an Ollama on 11434, say) is still the network."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        with pytest.raises(conftest.NetworkAccessBlocked):
            socket.create_connection(listener.getsockname(), timeout=1)
        with pytest.raises(conftest.NetworkAccessBlocked):
            socket.socket().connect(listener.getsockname())
    finally:
        listener.close()


def test_the_windows_emulation_connects_through_the_guard():
    """Replays CPython's socketpair fallback: a listener on 127.0.0.1 and a
    client connecting to it from inside socketpair. The connect is allowed
    there, and the flag is cleared afterwards, so the next connect is refused."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def emulated_socketpair():
        client = socket.socket()
        client.connect(listener.getsockname())
        server, _ = listener.accept()
        return server, client

    socketpair = conftest._socketpair_allowing_its_own_connect(emulated_socketpair)
    try:
        server, client = socketpair()
        client.close()
        server.close()
        with pytest.raises(conftest.NetworkAccessBlocked):
            socket.socket().connect(listener.getsockname())
    finally:
        listener.close()


def test_a_remote_connection_is_refused_before_any_packet_leaves():
    with pytest.raises(conftest.NetworkAccessBlocked):
        socket.create_connection(("example.com", 80), timeout=1)
