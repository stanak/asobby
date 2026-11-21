import socket
import struct

import pytest
import host_probe as mod
from probe_transport import ProbeCache

TARGET = ("192.0.2.1", 10800)
RELAY = ("192.0.2.2", 14763)
NAT = (TARGET[0], 19000)
WAITING = bytes.fromhex("0701000000")


class Transport:
    def __init__(self, *, giuroll=False, vanilla=False, ap=False):
        self.giuroll, self.vanilla, self.ap = giuroll, vanilla, ap
        self.sent = []

    def getsockname(self):
        return ("0.0.0.0", 31000)

    def exchange(self, target, payload, accept, timeout):
        self.sent.append((payload, target))
        response = None
        if target == RELAY:
            if payload == b"\x00":
                response = payload
            else:
                assert payload == struct.pack("!H4sH", 31000, socket.inet_aton(TARGET[0]), TARGET[1])
                response = struct.pack("!HH4s", TARGET[1], NAT[1], socket.inet_aton(TARGET[0]))
        elif target == (NAT if self.ap else TARGET):
            if payload == mod.GIUROLL_PING and self.giuroll:
                response = mod.GIUROLL_PONG
            elif payload == mod.soku_echo_packet() and self.vanilla:
                response = WAITING
        return response if response is not None and accept(response) else None


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(mod, "_rtt_cache", ProbeCache())
    monkeypatch.setattr(mod.socket, "gethostbyname", lambda _: RELAY[0])


@pytest.mark.parametrize("known", [False, True])
def test_giuroll_ping_avoids_spectator_requests(monkeypatch, known):
    sock = Transport(giuroll=True, vanilla=True)
    monkeypatch.setattr(mod, "get_transport", lambda: sock)
    assert mod.probe_rtt_ms(*TARGET, giuroll=known) >= 1
    assert sock.sent == [(mod.GIUROLL_PING, TARGET)]
    assert mod.probe_rtt_ms(*TARGET, giuroll=known) >= 1
    assert len(sock.sent) == 1


def test_known_giuroll_loss_never_falls_back_to_spectating(monkeypatch):
    sock = Transport(vanilla=True)
    monkeypatch.setattr(mod, "get_transport", lambda: sock)
    assert mod.probe_rtt_ms(*TARGET, giuroll=True) is None
    assert sock.sent == [(mod.GIUROLL_PING, TARGET)] * 2


def test_vanilla_fallback_and_rtt_excludes_discovery_wait(monkeypatch):
    sock = Transport(vanilla=True)
    monkeypatch.setattr(mod, "get_transport", lambda: sock)
    now = [100.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    original = sock.exchange
    def exchange(target, payload, accept, timeout):
        now[0] += 0.35 if payload == mod.GIUROLL_PING else 0.012
        return original(target, payload, accept, timeout)
    sock.exchange = exchange
    assert 11 <= mod.probe_rtt_ms(*TARGET) <= 12
    assert sock.sent == [(mod.GIUROLL_PING, TARGET), (mod.soku_echo_packet(), TARGET)]


def test_autopunch_uses_same_transport_and_nat_endpoint(monkeypatch):
    sock = Transport(giuroll=True, ap=True)
    monkeypatch.setattr(mod, "get_transport", lambda: sock)
    assert mod.probe_rtt_ms(*TARGET, autopunch=True, giuroll=True) >= 1
    assert sock.sent[-1] == (mod.GIUROLL_PING, NAT)
    assert len(sock.sent) == 3


def test_failed_ap_fallback_reuses_recent_direct_failure(monkeypatch):
    sock = Transport()
    monkeypatch.setattr(mod, "get_transport", lambda: sock)
    assert mod.probe_rtt_ms(*TARGET, giuroll=True) is None
    assert mod.probe_rtt_ms(*TARGET, giuroll=True, autopunch=True) is None
    assert sock.sent.count((mod.GIUROLL_PING, TARGET)) == 2


@pytest.mark.parametrize("data", [b"\x07", b"\x08", b"\x07" + bytes([2, 0, 0, 0]), mod.GIUROLL_PONG])
def test_spectator_reply_validation(data):
    assert not mod.is_valid_reply(data)
