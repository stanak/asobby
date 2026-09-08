"""No real network: exercise wire parsing, NAT sequencing and failure modes."""
import socket
import struct
import threading

import pytest

import udp_probe as mod

HOST = "93.184.216.34"
TARGET = (HOST, 10800)
RELAY = ("8.8.8.8", 14763)
ECHO = b"\x05probe"
WAITING = b"\x07\x01\x00\x00\x00"
BUSY = b"\x08" + bytes(4) + b"\x02\x00" + struct.pack("!H4s", 10800, socket.inet_aton("1.1.1.1")) + bytes(56)


@pytest.mark.parametrize("addr", [
    "127.0.0.1:10800", "0.0.0.0:10800", "10.0.0.1:10800", "172.16.0.1:80",
    "192.168.0.1:80", "169.254.169.254:80", "100.64.0.1:80", "224.0.0.1:80",
    "255.255.255.255:80", "240.0.0.1:80", "192.0.2.1:80", "198.18.0.1:80",
    "localhost:80", "example.com:80", "[::1]:80", "127.1:80", "0177.0.0.1:80",
    "2130706433:80", "93.184.216.34:0", "93.184.216.34:65536", "93.184.216.34:+80",
    "93.184.216.34:８０",
])
def test_only_public_literal_ipv4_ports(addr):
    with pytest.raises(ValueError):
        mod.public_address(addr)


def test_normalize_address():
    assert mod.public_address(HOST + ":010800") == TARGET


@pytest.mark.parametrize("packet,expected", [
    (WAITING, "waiting"), (b"\x07\x00\x00\x00\x00", "unknown"), (BUSY, "connecting"),
    (b"\x06" + bytes(80), "connecting"), (b"", None), (b"\x07", None),
    (b"\x07\x00\x00\x00\x01", None), (b"\x08" + bytes(20), None),
    (b"\x6d\x61", None), (b"\x06" + bytes(10), None),
])
def test_decode_state_not_just_first_byte(packet, expected):
    assert mod.game_state(packet) == expected


class Socket:
    def __init__(self, *, direct=False, ap=False, giu=False, echo=True, relay_up=True, registered=True):
        self.direct, self.ap, self.giu, self.echo = direct, ap, giu, echo
        self.relay_up, self.registered = relay_up, registered
        self.sent = []
        self.responses = []
        self.closed = False
        self.bound = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def setsockopt(self, *args):
        pass

    def bind(self, addr):
        self.bound = addr

    def getsockname(self):
        return ("0.0.0.0", 10800)

    def settimeout(self, seconds):
        assert 0 < seconds <= 0.71

    def sendto(self, payload, target):
        self.sent.append((payload, target))
        if target == RELAY:
            if payload == b"\x00" and self.relay_up:
                self.responses.append((b"\x00", RELAY))
            elif len(payload) == 8 and self.registered:
                assert payload == struct.pack("!H4sH", 10800, socket.inet_aton(HOST), 10800)
                self.responses.append((struct.pack("!HH4s", 10800, 19000, socket.inet_aton(HOST)), RELAY))
        elif self.direct and target == TARGET or self.ap and target == (HOST, 19000):
            if payload == ECHO and self.echo:
                self.responses.append((WAITING, target))
            elif payload == b"\x6c\x00" and self.giu:
                self.responses.append((b"\x6d\x61", target))

    def recvfrom(self, size):
        if self.responses:
            return self.responses.pop(0)
        raise socket.timeout()


def run(monkeypatch, sock, **kwargs):
    monkeypatch.setattr(socket, "socket", lambda *args: sock)
    monkeypatch.setattr(socket, "gethostbyname", lambda host: RELAY[0])
    return mod.check(HOST + ":10800", echo=ECHO, lock=threading.Lock(),
                     bind_host="0.0.0.0", bind_port=10800, relay="relay.example:14763", **kwargs)


def test_direct_giuroll_and_no_relay_side_effect(monkeypatch):
    sock = Socket(direct=True, giu=True)
    result = run(monkeypatch, sock)
    assert result == mod.Result(alive=True, state="waiting", giuroll=True, direct=True)
    assert all(target == TARGET for _, target in sock.sent)
    assert sock.closed


def test_giuroll_pong_is_liveness_not_waiting(monkeypatch):
    result = run(monkeypatch, Socket(direct=True, giu=True, echo=False))
    assert result.alive and result.giuroll and result.state == "unknown"


def test_absent_giuroll_reply_does_not_fail_normal_host(monkeypatch):
    result = run(monkeypatch, Socket(direct=True))
    assert result.alive and not result.giuroll


@pytest.mark.parametrize("ap,prefer_ap", [(False, False), (True, False), (True, True)])
def test_new_giuroll_is_detected_on_silence_between_periodic_checks(monkeypatch, ap, prefer_ap):
    sock = Socket(direct=not ap, ap=ap, giu=True, echo=False)
    result = run(monkeypatch, sock, detect_giuroll=False, prefer_autopunch=prefer_ap)
    assert result == mod.Result(alive=True, giuroll=True, direct=not ap, autopunch=ap)
    target = (HOST, 19000) if ap else TARGET
    assert (b"\x6c\x00", target) in sock.sent
    if not ap:
        assert all(destination == TARGET for _, destination in sock.sent)
    elif prefer_ap:
        assert (ECHO, TARGET) not in sock.sent


def test_healthy_host_keeps_periodic_giuroll_detection_cadence(monkeypatch):
    sock = Socket(direct=True, giu=True)
    result = run(monkeypatch, sock, detect_giuroll=False)
    assert result.alive and not result.giuroll
    assert sock.sent == [(ECHO, TARGET)]


@pytest.mark.parametrize("sender,payload", [
    ((HOST, 1), b"\x6d\x61"), (("1.1.1.1", 10800), b"\x6d\x61"),
    (TARGET, b"\x6d"), (TARGET, b"\x6d\x61\x00"), (TARGET, b"\x07\x01\x00\x00\x00"),
])
def test_fallback_requires_exact_pong_from_requested_endpoint(monkeypatch, sender, payload):
    sock = Socket(registered=False)
    original = sock.sendto
    def send(data, target):
        original(data, target)
        if data == b"\x6c\x00":
            sock.responses.append((payload, sender))
    sock.sendto = send
    result = run(monkeypatch, sock, detect_giuroll=False)
    assert not result.alive and not result.giuroll and not result.inconclusive


def test_ap_lookup_and_game_probe_share_socket_and_direct_goes_first(monkeypatch):
    sock = Socket(ap=True, giu=True)
    result = run(monkeypatch, sock)
    assert result.alive and result.autopunch and result.giuroll and not result.direct
    assert sock.sent[:2] == [(ECHO, TARGET), (b"\x6c\x00", TARGET)]
    assert sock.sent[2][1] == RELAY
    assert sock.sent[-1] == (b"\x6c\x00", (HOST, 19000))


def test_known_ap_does_not_misclassify_its_punched_hole_as_direct(monkeypatch):
    sock = Socket(direct=True, ap=True)
    result = run(monkeypatch, sock, prefer_autopunch=True, detect_giuroll=False)
    assert result.autopunch and not result.direct
    assert (ECHO, TARGET) not in sock.sent


def test_relay_registration_alone_does_not_prove_alive(monkeypatch):
    result = run(monkeypatch, Socket())
    assert not result.alive and not result.inconclusive


def test_relay_outage_is_inconclusive(monkeypatch):
    result = run(monkeypatch, Socket(relay_up=False))
    assert not result.alive and result.inconclusive


def test_invalid_source_port_ip_and_payload_are_ignored():
    sock = Socket()
    sock.responses = [
        (WAITING, (HOST, 1)), (WAITING, ("1.1.1.1", 10800)),
        (b"\x07", TARGET), (b"\x6d\x61", TARGET), (WAITING, TARGET),
    ]
    assert mod.exchange(sock, TARGET, ECHO, lambda data: mod.game_state(data) is not None) == WAITING


def test_local_bind_error_is_not_a_dead_host(monkeypatch):
    sock = Socket()
    def fail(addr):
        raise OSError("address not available")
    sock.bind = fail
    assert run(monkeypatch, sock).inconclusive


def test_relay_mapping_for_another_host_never_redirects_probe(monkeypatch):
    sock = Socket()
    original = sock.sendto
    def send(payload, target):
        original(payload, target)
        if target == RELAY and len(payload) == 8:
            sock.responses = [(struct.pack("!HH4s", 10800, 19000, socket.inet_aton("127.0.0.1")), RELAY)]
    sock.sendto = send
    result = run(monkeypatch, sock)
    assert not result.alive
    assert all(target in {TARGET, RELAY} for _, target in sock.sent)
