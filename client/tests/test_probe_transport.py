"""Portable transport tests: no game, relay or external network is contacted."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue, Empty
import socket
import threading

import pytest
import probe_transport as mod


class Socket:
    def __init__(self):
        self.incoming, self.sent = Queue(), Queue()
        self.closed = False
        self.bound = None

    def ioctl(self, *args):
        pass

    def bind(self, addr):
        self.bound = addr

    def settimeout(self, timeout):
        self.timeout = timeout

    def getsockname(self):
        return ("0.0.0.0", 31000)

    def sendto(self, data, target):
        assert not self.closed
        self.sent.put((data, target))

    def recvfrom(self, size):
        try:
            item = self.incoming.get(timeout=self.timeout)
        except Empty:
            raise socket.timeout() from None
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True
        self.incoming.put(OSError("closed"))


@pytest.fixture
def transport(monkeypatch):
    sock = Socket()
    created = []

    def create(*args):
        created.append(sock)
        return sock

    monkeypatch.setattr(mod.socket, "socket", create)
    obj = mod.DatagramTransport()
    with ThreadPoolExecutor(max_workers=4) as executor:
        try:
            yield obj, sock, created, executor
        finally:
            obj.close()


def start(executor, obj, target, timeout=1):
    return executor.submit(obj.exchange, target, b"ping", lambda b: b == b"pong", timeout)


def test_socket_survives_success_timeout_and_late_packets(transport):
    obj, sock, created, executor = transport
    target = ("192.0.2.1", 10800)
    for _ in range(2):
        job = start(executor, obj, target)
        assert sock.sent.get(timeout=1) == (b"ping", target)
        sock.incoming.put((b"pong", target))
        assert job.result(timeout=1) == b"pong"
        assert not sock.closed
    job = start(executor, obj, target, timeout=0.02)
    sock.sent.get(timeout=1)
    assert job.result(timeout=1) is None
    assert not sock.closed and len(created) == 1
    # Late traffic has an open destination even with no pending request.
    sock.incoming.put((b"pong", target))
    other = ("192.0.2.2", 10800)
    job = start(executor, obj, other)
    sock.sent.get(timeout=1)
    sock.incoming.put((b"pong", other))
    assert job.result(timeout=1) == b"pong"


def test_dispatch_checks_ip_port_payload_and_handles_udp_errors(transport):
    obj, sock, _, executor = transport
    target = ("192.0.2.1", 10800)
    job = start(executor, obj, target)
    sock.sent.get(timeout=1)
    sock.incoming.put(ConnectionResetError("ICMP"))
    sock.incoming.put((b"pong", (target[0], 10801)))
    sock.incoming.put((b"pong", ("192.0.2.2", target[1])))
    sock.incoming.put((b"wrong", target))
    sock.incoming.put((b"pong", target))
    assert job.result(timeout=1) == b"pong"
    assert not sock.closed


def test_unsupported_wine_udp_ioctl_does_not_disable_probing(transport, monkeypatch):
    obj, sock, _, executor = transport
    monkeypatch.setattr(mod.socket, "SIO_UDP_CONNRESET", 123, raising=False)
    def unsupported(*args):
        raise OSError("unsupported ioctl")
    sock.ioctl = unsupported
    target = ("192.0.2.1", 10800)
    job = start(executor, obj, target)
    sock.sent.get(timeout=1)
    sock.incoming.put((b"pong", target))
    assert job.result(timeout=1) == b"pong"


def test_different_hosts_run_concurrently_on_one_socket(transport):
    obj, sock, created, executor = transport
    targets = [("192.0.2.1", 10800), ("192.0.2.1", 10801)]
    jobs = [start(executor, obj, target) for target in targets]
    sent = [sock.sent.get(timeout=1), sock.sent.get(timeout=1)]
    assert {entry[1] for entry in sent} == set(targets)
    for target in reversed(targets):
        sock.incoming.put((b"pong", target))
    assert [job.result(timeout=1) for job in jobs] == [b"pong", b"pong"]
    assert len(created) == 1


def test_same_endpoint_wait_consumes_deadline_without_sending(transport):
    obj, sock, _, executor = transport
    target = ("192.0.2.1", 10800)
    first = start(executor, obj, target)
    sock.sent.get(timeout=1)
    second = start(executor, obj, target, timeout=0.02)
    assert second.result(timeout=1) is None
    assert sock.sent.empty()
    sock.incoming.put((b"pong", target))
    assert first.result(timeout=1) == b"pong"


def test_close_wakes_pending_requests(transport):
    obj, sock, _, executor = transport
    job = start(executor, obj, ("192.0.2.1", 10800))
    sock.sent.get(timeout=1)
    obj.close()
    with pytest.raises(OSError):
        job.result(timeout=1)
    assert sock.closed


def test_cache_shares_inflight_and_completed_measurements():
    cache = mod.ProbeCache()
    started, release = threading.Event(), threading.Event()
    calls = []

    def probe():
        calls.append(1)
        started.set()
        assert release.wait(1)
        return 12

    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = [executor.submit(cache.run, "host", probe) for _ in range(4)]
        assert started.wait(1)
        release.set()
        assert [job.result(timeout=1) for job in jobs] == [12] * 4
    assert cache.run("host", probe) == 12
    assert len(calls) == 1


def test_cache_expires_and_has_bounded_storage(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    cache = mod.ProbeCache(ttl=5, limit=2)
    assert cache.run("a", lambda: 1) == 1
    assert cache.run("a", lambda: 2) == 1
    now[0] += 6
    assert cache.run("a", lambda: 2) == 2
    cache.run("b", lambda: None)
    cache.run("c", lambda: None)
    assert list(cache._results) == ["b", "c"]


def test_failed_measurement_releases_single_flight():
    cache = mod.ProbeCache()
    def fail():
        raise OSError("failed")
    with pytest.raises(OSError):
        cache.run("a", fail)
    assert cache.run("a", lambda: 4) == 4


def test_client_server_transport_sources_are_identical():
    root = Path(__file__).resolve().parents[2]
    assert (root / "client/src/probe_transport.py").read_bytes() == (
        root / "server/app/probe_transport.py"
    ).read_bytes()


def test_loopback_port_survives_late_reply_and_next_check():
    """Real loopback sockets, including Winsock in the Windows release tests."""
    obj = mod.DatagramTransport("127.0.0.1", 0)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        peer.bind(("127.0.0.1", 0))
        peer.settimeout(1)
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                first = start(executor, obj, peer.getsockname(), timeout=0.03)
                data, sender = peer.recvfrom(128)
                assert data == b"ping" and first.result(timeout=1) is None
                peer.sendto(b"pong", sender)  # Deliberately after the probe timeout.
                peer.settimeout(0.05)
                with pytest.raises(socket.timeout):
                    peer.recvfrom(128)  # Not ConnectionResetError / ICMP on Windows.
                peer.settimeout(1)
                second = start(executor, obj, peer.getsockname())
                data, sender2 = peer.recvfrom(128)
                assert sender2 == sender and data == b"ping"
                peer.sendto(b"pong", sender2)
                assert second.result(timeout=1) == b"pong"
            finally:
                obj.close()
