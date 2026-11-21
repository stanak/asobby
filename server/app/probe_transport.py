"""Persistent UDP probes. Keep this file identical in client/src and server/app.

A single reader drains late replies even between checks. Only an outstanding
request with the exact remote IP, port and expected payload receives a packet.
Closing a probe socket on every timeout can provoke ICMP errors at the game.
"""
from __future__ import annotations

import atexit
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _Pending:
    accept: Callable[[bytes], bool]
    done: threading.Event = field(default_factory=threading.Event)
    reply: bytes | None = None
    error: OSError | None = None


class DatagramTransport:
    def __init__(self, bind_host="0.0.0.0", bind_port=0):
        self.bind_address = (bind_host, bind_port)
        self._condition = threading.Condition()
        self._pending = {}
        self._socket = None
        self._thread = None
        self._closed = False

    def _start(self):
        # Called with _condition held; only one reader may own this UDP port.
        if self._closed:
            raise OSError("probe transport closed")
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, "SIO_UDP_CONNRESET"):
                try:
                    sock.ioctl(socket.SIO_UDP_CONNRESET, False)
                except OSError:
                    pass  # Optional on Wine; the reader also handles ICMP errors.
            sock.bind(self.bind_address)
            sock.settimeout(0.25)
        except BaseException:
            sock.close()
            raise
        self._socket = sock
        self._thread = threading.Thread(
            target=self._receive, args=(sock,), name="asobby-udp-receiver", daemon=True,
        )
        self._thread.start()

    def getsockname(self):
        with self._condition:
            self._start()
            return self._socket.getsockname()

    def exchange(self, target, payload, accept, timeout=0.35):
        target = (target[0], target[1])
        deadline = time.monotonic() + timeout
        with self._condition:
            # Serialise same-endpoint packets (including relay requests), not
            # unrelated hosts. Waiting for another check consumes this budget.
            while target in self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if time.monotonic() >= deadline:
                return None
            self._start()
            pending = _Pending(accept)
            self._pending[target] = pending
            try:
                self._socket.sendto(payload, target)
            except OSError:
                self._pending.pop(target, None)
                self._condition.notify_all()
                raise
        try:
            pending.done.wait(max(0, deadline - time.monotonic()))
            if pending.error is not None:
                raise pending.error
            return pending.reply
        finally:
            with self._condition:
                if self._pending.get(target) is pending:
                    self._pending.pop(target)
                self._condition.notify_all()

    def _receive(self, sock):
        while True:
            try:
                data, sender = sock.recvfrom(4096)
            except socket.timeout:
                with self._condition:
                    if self._closed:
                        return
                continue
            except (ConnectionResetError, ConnectionRefusedError):
                # A remote UDP error is not a reason to stop draining replies.
                continue
            except OSError as exc:
                with self._condition:
                    if self._socket is sock:
                        self._socket = None
                        for pending in self._pending.values():
                            pending.error = exc
                            pending.done.set()
                    sock.close()
                return
            with self._condition:
                pending = self._pending.get(sender[:2])
                if pending is None or pending.done.is_set():
                    continue  # Idle/late/duplicate packet: drain without replying.
                try:
                    accepted = pending.accept(data)
                except (ValueError, IndexError, TypeError):
                    accepted = False
                if accepted:
                    pending.reply = data
                    pending.done.set()

    def close(self):
        with self._condition:
            self._closed = True
            for pending in self._pending.values():
                pending.error = OSError("probe transport closed")
                pending.done.set()
            self._condition.notify_all()
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)


class ProbeCache:
    """Bounded short-lived results and single-flight for overlapping callers."""
    def __init__(self, ttl=5.0, limit=256):
        self.ttl, self.limit = ttl, limit
        self._condition = threading.Condition()
        self._running = set()
        self._results = OrderedDict()

    def run(self, key, operation):
        with self._condition:
            while key in self._running:
                self._condition.wait()
            cached = self._results.get(key)
            if cached is not None and time.monotonic() < cached[0]:
                return cached[1]
            self._running.add(key)
        try:
            result = operation()
            with self._condition:
                self._results[key] = (time.monotonic() + self.ttl, result)
                self._results.move_to_end(key)
                while len(self._results) > self.limit:
                    self._results.popitem(last=False)
            return result
        finally:
            with self._condition:
                self._running.discard(key)
                self._condition.notify_all()


_transports = {}
_transports_lock = threading.Lock()


def get_transport(bind_host=None, bind_port=0):
    # No configured bind means an ephemeral port, kept for the process lifetime.
    key = (bind_host or "0.0.0.0", bind_port if bind_host else 0)
    with _transports_lock:
        if key not in _transports:
            _transports[key] = DatagramTransport(*key)
        return _transports[key]


def close_transports():
    with _transports_lock:
        transports = list(_transports.values())
        _transports.clear()
    for transport in transports:
        transport.close()


atexit.register(close_transports)
