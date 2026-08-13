import base64
import hashlib
import json
import socket
import ssl
import threading
from datetime import datetime, timedelta

import pytest

from client import agent_runtime
from client.agent_config import AgentConfig, DeviceCredential
from client.agent_runtime import (
    AgentRuntime,
    AgentState,
    AuthRejected,
    ManagedConnector,
    RetryPolicy,
    send_frame,
)
from client.managed_identity import AgentCertificateIdentity
from client.transport import build_proof


def frame(payload):
    return len(payload).to_bytes(4, "big") + payload


def json_frame(message):
    return frame(json.dumps(message, sort_keys=True, separators=(",", ":")).encode())


def decode_frame(packet):
    size = int.from_bytes(packet[:4], "big")
    assert size == len(packet) - 4
    return packet[4:]


@pytest.fixture
def config():
    return AgentConfig(
        controller_host="controller.test",
        managed_port=5443,
        enrollment_port=5444,
        tls_cert_sha256=hashlib.sha256(b"peer-der").hexdigest(),
        connect_timeout=2,
        io_poll_interval=0.25,
        controller_ping_interval=1,
        controller_pong_timeout=1,
        agent_read_deadline=3,
        retry_base=1,
        retry_max=30,
        retry_jitter=0,
    )


@pytest.fixture
def credential():
    return DeviceCredential("agent-1", "key-1", b"s" * 32)


@pytest.fixture
def valid_identity():
    return AgentCertificateIdentity(
        agent_id="11111111-1111-4111-8111-111111111111",
        certificate_pem=b"certificate",
        chain_pem=b"chain",
        private_key_pem=b"private-key",
        certificate_serial="1",
        certificate_not_after="2026-11-11T00:00:00Z",
    )


class RecordingRenewer:
    def __init__(self, replacement=None, error=None):
        self.replacement = replacement
        self.error = error
        self.calls = []

    def __call__(self, _config, identity, _store):
        self.calls.append(identity.agent_id)
        if self.error is not None:
            raise self.error
        return self.replacement or identity


def test_runtime_renews_only_at_30_days_or_less(valid_identity, config):
    renewer = RecordingRenewer()
    runtime = AgentRuntime(
        config,
        valid_identity,
        identity_store=object(),
        connector=ScriptedConnector(),
        renewer=renewer,
    )
    expiry = datetime.fromisoformat(
        valid_identity.certificate_not_after.replace("Z", "+00:00")
    )

    assert runtime.prepare_identity(now=expiry - timedelta(days=31)) is True
    assert renewer.calls == []
    assert runtime.prepare_identity(now=expiry - timedelta(days=30)) is True
    assert renewer.calls == [valid_identity.agent_id]


def test_runtime_continues_on_failed_renewal_until_current_certificate_expires(
    valid_identity, config
):
    events = []
    renewer = RecordingRenewer(error=OSError("offline"))
    runtime = AgentRuntime(
        config,
        valid_identity,
        identity_store=object(),
        connector=ScriptedConnector(),
        renewer=renewer,
        event_sink=events.append,
    )
    expiry = datetime.fromisoformat(
        valid_identity.certificate_not_after.replace("Z", "+00:00")
    )

    assert runtime.prepare_identity(now=expiry - timedelta(days=1)) is True
    assert [event["event"] for event in events] == ["CERTIFICATE_RENEWAL_FAILED"]
    assert runtime.prepare_identity(now=expiry) is False
    assert [event["event"] for event in events][-1] == "CERTIFICATE_EXPIRED"


class FakeSocket:
    def __init__(self, incoming=()):
        self.incoming = list(incoming)
        self.sent = []
        self.timeouts = []
        self.shutdown_calls = []
        self.closed = False
        self.on_recv = None

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def recv(self, _size):
        if self.on_recv is not None:
            self.on_recv()
        item = self.incoming.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item()
        if len(item) > _size:
            self.incoming.insert(0, item[_size:])
            return item[:_size]
        return item

    def sendall(self, packet):
        self.sent.append(packet)

    def shutdown(self, how):
        self.shutdown_calls.append(how)

    def close(self):
        self.closed = True

    @property
    def sent_frames(self):
        return [decode_frame(packet) for packet in self.sent]


class FakeRetryPolicy:
    def __init__(self, delay=0):
        self.delay = delay
        self.next_calls = 0
        self.reset_calls = 0
        self.on_next = None

    def next_delay(self):
        self.next_calls += 1
        if self.on_next is not None:
            self.on_next()
        return self.delay

    def reset(self):
        self.reset_calls += 1


class ScriptedConnector:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0
        self.states = []
        self.runtime = None

    def connect(self, _config, _credential):
        self.calls += 1
        if self.runtime is not None:
            self.states.append(self.runtime.state)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def test_retry_sequence_and_reset():
    policy = RetryPolicy(base=1, maximum=30, jitter=0, random=lambda: 0.5)
    assert [policy.next_delay() for _ in range(7)] == [1, 2, 4, 8, 16, 30, 30]
    policy.reset()
    assert policy.next_delay() == 1


def test_retry_jitter_uses_symmetric_bounds():
    low = RetryPolicy(base=10, maximum=30, jitter=0.2, random=lambda: 0)
    high = RetryPolicy(base=10, maximum=30, jitter=0.2, random=lambda: 1)
    assert low.next_delay() == 8
    assert high.next_delay() == 12


def test_retry_stays_capped_for_long_running_agent():
    policy = RetryPolicy(base=1.0, maximum=30.0, jitter=0, random=lambda: 0.5)
    assert [policy.next_delay() for _ in range(2_000)][-1] == 30


def test_send_frame_uses_transport_framing():
    conn = FakeSocket()
    send_frame(conn, b"PONG")
    assert conn.sent == [b"\0\0\0\x04PONG"]


def runtime_for_session(config, conn, *, clock=lambda: 0):
    connector = ScriptedConnector(conn)
    return AgentRuntime(
        config, DeviceCredential("a", "k", b"s"), connector=connector, clock=clock
    )


def test_socket_timeout_is_poll_tick_not_disconnect(config):
    conn = FakeSocket([TimeoutError(), frame(b"PING"), TimeoutError()])
    runtime = runtime_for_session(config, conn)
    conn.incoming[-1] = lambda: (
        runtime.stop_event.set(),
        (_ for _ in ()).throw(TimeoutError()),
    )[1]

    runtime.run_one_session(conn)

    assert conn.sent_frames == [b"PONG"]
    assert conn.timeouts == [config.io_poll_interval]


def test_partial_frame_survives_timeout(config):
    packet = frame(b"PING")
    conn = FakeSocket([packet[:2], TimeoutError(), packet[2:], b""])
    runtime = runtime_for_session(config, conn)

    with pytest.raises(ConnectionError):
        runtime.run_one_session(conn)

    assert conn.sent_frames == [b"PONG"]


def test_unexpected_online_frame_is_protocol_error(config):
    conn = FakeSocket([frame(b"COMMAND")])
    runtime = runtime_for_session(config, conn)

    with pytest.raises(ValueError, match="unexpected online frame"):
        runtime.run_one_session(conn)

    assert conn.sent == []


def test_send_timeout_is_session_failure_not_recv_poll_tick(config):
    class SendTimeoutSocket(FakeSocket):
        def sendall(self, _packet):
            raise TimeoutError()

    conn = SendTimeoutSocket([frame(b"PING")])
    runtime = runtime_for_session(config, conn)

    with pytest.raises(socket.timeout):
        runtime.run_one_session(conn)


def test_deadline_uses_injected_monotonic_clock(config):
    ticks = iter([10, 13])
    conn = FakeSocket([TimeoutError()])
    runtime = runtime_for_session(config, conn, clock=lambda: next(ticks))

    with pytest.raises(TimeoutError, match="heartbeat deadline"):
        runtime.run_one_session(conn)


def test_only_complete_ping_resets_deadline(config):
    ticks = iter([0, 2, 3])
    conn = FakeSocket([frame(b"PING")[:2], TimeoutError()])
    runtime = runtime_for_session(config, conn, clock=lambda: next(ticks))

    with pytest.raises(TimeoutError, match="heartbeat deadline"):
        runtime.run_one_session(conn)


def test_complete_ping_extends_deadline_with_advancing_clock(config):
    ticks = iter([0, 2, 4, 5])
    conn = FakeSocket([frame(b"PING"), TimeoutError()])
    runtime = runtime_for_session(config, conn, clock=lambda: next(ticks))

    with pytest.raises(TimeoutError, match="heartbeat deadline"):
        runtime.run_one_session(conn)

    assert conn.incoming == []
    assert conn.sent_frames == [b"PONG"]


def test_online_decoder_rejects_frame_over_64_kib(config):
    conn = FakeSocket([(65537).to_bytes(4, "big")])
    runtime = runtime_for_session(config, conn)

    with pytest.raises(ValueError, match="frame too large"):
        runtime.run_one_session(conn)


class FakeRawSocket:
    def __init__(self, events):
        self.events = events
        self.closed = False
        self.shutdown_calls = []

    def settimeout(self, timeout):
        self.events.append(("raw-timeout", timeout))

    def connect(self, address):
        self.events.append(("connect", address))

    def shutdown(self, how):
        self.shutdown_calls.append(how)
        self.events.append(("raw-shutdown", how))

    def close(self):
        self.closed = True
        self.events.append("raw-close")


class FakeTlsSocket(FakeSocket):
    def __init__(self, incoming, events, certificate=b"peer-der"):
        super().__init__(incoming)
        self.events = events
        self.certificate = certificate
        self.close_error = None

    def settimeout(self, timeout):
        self.events.append(("tls-timeout", timeout))
        super().settimeout(timeout)

    def do_handshake(self):
        self.events.append("handshake")

    def getpeercert(self, *, binary_form):
        assert binary_form is True
        self.events.append("peer-cert")
        return self.certificate

    def sendall(self, packet):
        self.events.append("send")
        super().sendall(packet)

    def recv(self, size):
        self.events.append("recv")
        return super().recv(size)

    def shutdown(self, how):
        self.events.append(("tls-shutdown", how))
        super().shutdown(how)

    def close(self):
        self.events.append("tls-close")
        super().close()
        if self.close_error is not None:
            raise self.close_error


class FakeTlsContext:
    def __init__(self, tls, events):
        self.tls = tls
        self.events = events

    def wrap_socket(self, raw, *, server_hostname, do_handshake_on_connect):
        self.events.append(("wrap", raw, server_hostname, do_handshake_on_connect))
        return self.tls


def make_connector(config, credential, result="AUTH_OK", *, fragments=False):
    events = []
    nonce = b"n" * 32
    challenge = json_frame(
        {"type": "CHALLENGE", "nonce": base64.b64encode(nonce).decode("ascii")}
    )
    auth_result = json_frame({"type": result})
    incoming = [challenge, auth_result]
    if fragments:
        incoming = [challenge[:2], challenge[2:], auth_result[:5], auth_result[5:]]
    tls = FakeTlsSocket(incoming, events)
    raw = FakeRawSocket(events)
    context = FakeTlsContext(tls, events)
    connector = ManagedConnector(
        socket_factory=lambda *_: (events.append("factory"), raw)[1],
        context_factory=lambda: context,
    )
    publications = []

    def publish(current, previous=None):
        publications.append((current, previous))
        events.append("publish-tls" if current is tls else "publish-raw")
        return True

    def clear(current):
        publications.append((None, current))
        events.append("clear")

    connector.set_socket_hooks(publish, clear)
    return connector, raw, tls, events, publications, nonce


def test_connector_publishes_raw_before_connect_and_tls_before_handshake(
    config, credential
):
    connector, raw, tls, events, publications, nonce = make_connector(
        config, credential
    )

    assert connector.connect(config, credential) is tls

    assert events.index("publish-raw") < events.index(
        ("connect", (config.controller_host, 5443))
    )
    assert events.index(("raw-timeout", config.connect_timeout)) < events.index(
        ("connect", (config.controller_host, 5443))
    )
    assert ("wrap", raw, config.controller_host, False) in events
    assert events.index("publish-tls") < events.index("handshake")
    assert events.index(("tls-timeout", config.connect_timeout)) < events.index(
        "handshake"
    )
    assert publications == [(raw, None), (tls, raw)]
    hello = json.loads(decode_frame(tls.sent[0]))
    proof = json.loads(decode_frame(tls.sent[1]))
    assert hello == {
        "agent_id": credential.agent_id,
        "key_id": credential.key_id,
        "type": "HELLO",
        "version": 1,
    }
    expected = build_proof(
        credential.secret, 1, credential.agent_id, credential.key_id, nonce
    )
    assert proof == {
        "type": "AUTH_PROOF",
        "proof": base64.b64encode(expected).decode("ascii"),
    }
    assert not raw.closed and not tls.closed


def test_connector_decodes_fragmented_auth_frames(config, credential):
    connector, _, tls, _, _, _ = make_connector(config, credential, fragments=True)
    assert connector.connect(config, credential) is tls


def test_auth_reader_does_not_consume_first_online_frame(config, credential):
    connector, _, tls, _, _, _ = make_connector(config, credential)
    challenge, result = tls.incoming
    stopped = threading.Event()

    def stop_poll():
        stopped.set()
        raise TimeoutError()

    tls.incoming = [challenge, result + frame(b"PING"), stop_poll]

    assert connector.connect(config, credential) is tls
    runtime = AgentRuntime(config, credential, connector=ScriptedConnector(tls))
    runtime.stop_event = stopped
    runtime.run_one_session(tls)

    assert tls.sent_frames[-1] == b"PONG"


def test_default_connector_context_requires_tls_1_2_or_newer():
    context = agent_runtime._tls_client_context()
    assert context.minimum_version is ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is False
    assert context.verify_mode is ssl.CERT_NONE


def test_connector_rejects_missing_peer_certificate(config, credential):
    connector, _, tls, _, _, _ = make_connector(config, credential)
    tls.certificate = None

    with pytest.raises(ssl.SSLError, match="certificate unavailable"):
        connector.connect(config, credential)

    assert tls.sent == []
    assert tls.closed


@pytest.mark.parametrize(
    "challenge",
    [
        {"type": "CHALLENGE"},
        {"type": "CHALLENGE", "nonce": "", "extra": True},
        {"type": "WRONG", "nonce": ""},
        {"type": "CHALLENGE", "nonce": 1},
        {"type": "CHALLENGE", "nonce": "not-base64!"},
        {
            "type": "CHALLENGE",
            "nonce": base64.b64encode(b"n" * 32).decode("ascii") + "=",
        },
        {
            "type": "CHALLENGE",
            "nonce": base64.b64encode(b"n" * 31).decode("ascii"),
        },
    ],
)
def test_connector_rejects_malformed_challenge(config, credential, challenge):
    connector, _, tls, _, _, _ = make_connector(config, credential)
    tls.incoming = [json_frame(challenge)]

    with pytest.raises(ValueError):
        connector.connect(config, credential)

    assert tls.closed


def test_connector_normalizes_recursive_auth_json(config, credential):
    connector, _, tls, _, _, _ = make_connector(config, credential)
    nested = b"[" * 5000 + b"]" * 5000
    tls.incoming = [frame(b'{"type":"CHALLENGE","nonce":' + nested + b"}")]

    with pytest.raises(ValueError, match="invalid authentication message") as raised:
        connector.connect(config, credential)

    assert isinstance(raised.value.__cause__, RecursionError)
    assert tls.closed


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"type": "AUTH_OK", "extra": True},
        {"type": "WRONG"},
        {"type": 1},
    ],
)
def test_connector_rejects_malformed_auth_result(config, credential, result):
    connector, _, tls, _, _, _ = make_connector(config, credential)
    tls.incoming[-1] = json_frame(result)

    with pytest.raises(ValueError, match="authentication result"):
        connector.connect(config, credential)

    assert tls.closed


def test_connector_uses_fresh_socket_and_closes_before_clear(config, credential):
    events = []

    class RefusedRaw(FakeRawSocket):
        def connect(self, address):
            super().connect(address)
            raise ConnectionRefusedError()

    sockets = [RefusedRaw(events), RefusedRaw(events)]
    connector = ManagedConnector(
        socket_factory=lambda *_: sockets.pop(0),
        context_factory=lambda: pytest.fail("TLS should not start"),
    )
    cleared = []
    connector.set_socket_hooks(
        lambda current, _previous=None: True,
        lambda current: (cleared.append(current), events.append("clear")),
    )

    for _ in range(2):
        with pytest.raises(ConnectionRefusedError):
            connector.connect(config, credential)

    assert len({id(conn) for conn in cleared}) == 2
    assert all(conn.closed for conn in cleared)
    assert events[-2:] == ["raw-close", "clear"]


def test_authenticated_auth_reject_is_fatal_and_closed(config, credential):
    connector, _, tls, events, publications, _ = make_connector(
        config, credential, result="AUTH_REJECT"
    )

    with pytest.raises(AuthRejected):
        connector.connect(config, credential)

    assert tls.closed
    assert events[-2:] == ["tls-close", "clear"]
    assert publications[-1] == (None, tls)


def test_close_error_does_not_mask_authenticated_auth_reject(config, credential):
    connector, _, tls, events, publications, _ = make_connector(
        config, credential, result="AUTH_REJECT"
    )
    tls.close_error = OSError("close failed")

    with pytest.raises(AuthRejected):
        connector.connect(config, credential)

    assert tls.closed
    assert events[-2:] == ["tls-close", "clear"]
    assert publications[-1] == (None, tls)


def test_pin_mismatch_happens_before_hello_and_is_not_auth_rejected(config, credential):
    connector, _, tls, _, _, _ = make_connector(config, credential)
    tls.certificate = b"different"

    with pytest.raises(ssl.SSLError):
        connector.connect(config, credential)

    assert tls.sent == []
    assert tls.closed


def test_connector_uses_constant_time_pin_comparison(monkeypatch, config, credential):
    connector, _, _, _, _, _ = make_connector(config, credential)
    compared = []
    real_compare = agent_runtime.hmac.compare_digest

    def compare(left, right):
        compared.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(agent_runtime.hmac, "compare_digest", compare)
    connector.connect(config, credential)

    assert compared == [
        (hashlib.sha256(b"peer-der").hexdigest(), config.tls_cert_sha256)
    ]


def test_eof_before_authenticated_result_is_transient_oserror(config, credential):
    connector, _, tls, _, _, _ = make_connector(config, credential)
    tls.incoming = [b""]

    with pytest.raises(OSError) as raised:
        connector.connect(config, credential)

    assert not isinstance(raised.value, AuthRejected)


@pytest.mark.parametrize(
    ("mode", "error"),
    [("timeout", TimeoutError), ("unexpected-result", ValueError)],
)
def test_non_reject_auth_outcomes_are_transient_and_cleaned(
    config, credential, mode, error
):
    connector, _, tls, events, publications, _ = make_connector(
        config, credential, result="UNEXPECTED"
    )
    if mode == "timeout":
        tls.incoming = [TimeoutError()]

    with pytest.raises(error) as raised:
        connector.connect(config, credential)

    assert not isinstance(raised.value, AuthRejected)
    assert tls.closed
    assert events[-2:] == ["tls-close", "clear"]
    assert publications[-1] == (None, tls)


def test_auth_trickle_cannot_extend_connect_timeout(config, credential):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    connector, _, tls, _, _, _ = make_connector(config, credential)
    challenge = tls.incoming[0]
    tls.incoming = [bytes([byte]) for byte in challenge]
    original_recv = tls.recv

    def advancing_recv(size):
        clock.now += 0.75
        return original_recv(size)

    tls.recv = advancing_recv
    connector.clock = clock

    with pytest.raises(TimeoutError, match="authentication deadline"):
        connector.connect(config, credential)

    assert tls.closed


def test_late_complete_auth_reject_is_timeout_not_fatal(config, credential):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    connector, _, tls, _, _, _ = make_connector(
        config, credential, result="AUTH_REJECT"
    )
    original_recv = tls.recv
    receives = 0

    def recv(size):
        nonlocal receives
        packet = original_recv(size)
        receives += 1
        if receives == 4:
            clock.now = config.connect_timeout
        return packet

    tls.recv = recv
    connector.clock = clock

    with pytest.raises(TimeoutError, match="authentication deadline") as raised:
        connector.connect(config, credential)

    assert not isinstance(raised.value, AuthRejected)


def test_auth_rejection_stops_without_backoff(config, credential):
    connector = ScriptedConnector(AuthRejected())
    retry = FakeRetryPolicy()
    runtime = AgentRuntime(config, credential, connector=connector, retry_policy=retry)
    connector.runtime = runtime

    runtime.run()

    assert runtime.state is AgentState.STOPPED
    assert connector.calls == 1
    assert connector.states == [AgentState.CONNECTING]
    assert retry.next_calls == 0


@pytest.mark.parametrize(
    ("credential", "error", "message"),
    [
        (object(), TypeError, "DeviceCredential"),
        (DeviceCredential("", "key-1", b"s" * 32), ValueError, "agent_id"),
        (DeviceCredential("a" * 129, "key-1", b"s" * 32), ValueError, "agent_id"),
        (DeviceCredential(1, "key-1", b"s" * 32), ValueError, "agent_id"),
        (DeviceCredential("agent-1", "", b"s" * 32), ValueError, "key_id"),
        (DeviceCredential("agent-1", "k" * 129, b"s" * 32), ValueError, "key_id"),
        (DeviceCredential("agent-1", 1, b"s" * 32), ValueError, "key_id"),
        (DeviceCredential("agent-1", "key-1", b"s" * 31), ValueError, "secret"),
        (DeviceCredential("agent-1", "key-1", b"s" * 33), ValueError, "secret"),
        (DeviceCredential("agent-1", "key-1", bytearray(32)), ValueError, "secret"),
    ],
)
def test_invalid_local_credential_fails_before_socket(
    config, credential, error, message
):
    connector = ScriptedConnector()
    events = []
    runtime = AgentRuntime(
        config, credential, connector=connector, event_sink=events.append
    )

    with pytest.raises(error, match=message):
        runtime.run()

    assert connector.calls == 0
    assert runtime.state is AgentState.STOPPED
    assert [event for event in events if event["event"] == "CONNECTION_FAILURE"] == [
        {
            "event": "CONNECTION_FAILURE",
            "state": "STARTING",
            "attempt": 0,
            "category": "credential",
        }
    ]


@pytest.mark.parametrize("size", [1, 128])
def test_valid_credential_boundaries_reach_connector(config, size):
    connector = ScriptedConnector(AuthRejected())
    runtime = AgentRuntime(
        config,
        DeviceCredential("a" * size, "k" * size, b"s" * 32),
        connector=connector,
    )

    runtime.run()

    assert connector.calls == 1
    assert runtime.state is AgentState.STOPPED


def test_second_concurrent_run_is_rejected_without_second_owner(config, credential):
    entered = threading.Event()
    release = threading.Event()
    events = []

    class BlockingConnector:
        calls = 0

        def connect(self, _config, _credential):
            self.calls += 1
            entered.set()
            assert release.wait(1)
            raise OSError("offline")

    connector = BlockingConnector()
    runtime = AgentRuntime(
        config, credential, connector=connector, event_sink=events.append
    )
    owner = threading.Thread(target=runtime.run, name="runtime-owner")
    owner.start()
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError, match="already running"):
            runtime.run()
        assert connector.calls == 1
        runtime.stop()
        release.set()
        owner.join(1)
    finally:
        release.set()
        runtime.stop()
        owner.join(1)

    assert not owner.is_alive()
    assert runtime.state is AgentState.STOPPED
    assert [event["event"] for event in events].count("PROCESS_START") == 1
    assert [event["event"] for event in events].count("PROCESS_STOP") == 1


def test_stopped_runtime_has_no_outgoing_transition(config, credential):
    connector = ScriptedConnector(AuthRejected())
    events = []
    runtime = AgentRuntime(
        config, credential, connector=connector, event_sink=events.append
    )

    runtime.run()
    completed_events = list(events)
    transitions = []
    runtime._set_state = transitions.append
    runtime.run()

    assert connector.calls == 1
    assert events == completed_events
    assert transitions == []
    assert runtime.state is AgentState.STOPPED


def test_exception_class_not_message_selects_fatal_path(config, credential):
    connector = ScriptedConnector(
        OSError("AUTH_REJECT"), AuthRejected("temporary network error")
    )
    retry = FakeRetryPolicy()
    runtime = AgentRuntime(config, credential, connector=connector, retry_policy=retry)

    runtime.run()

    assert connector.calls == 2
    assert retry.next_calls == 1
    assert runtime.state is AgentState.STOPPED


def test_transient_session_closes_before_backoff_and_resets_retry(config, credential):
    conn = FakeSocket([b""])
    connector = ScriptedConnector(conn, AuthRejected())
    retry = FakeRetryPolicy()
    runtime = AgentRuntime(config, credential, connector=connector, retry_policy=retry)
    connector.runtime = runtime
    conn.on_recv = lambda: connector.states.append(runtime.state)
    retry.on_next = lambda: (
        connector.states.append(runtime.state),
        pytest.fail("socket entered backoff before close") if not conn.closed else None,
    )

    runtime.run()

    assert connector.states == [
        AgentState.CONNECTING,
        AgentState.ONLINE,
        AgentState.BACKOFF,
        AgentState.CONNECTING,
    ]
    assert retry.reset_calls == 1
    assert retry.next_calls == 1
    assert conn.closed


def test_exact_owner_state_sequence(config, credential):
    conn = FakeSocket([b""])
    connector = ScriptedConnector(conn, AuthRejected())
    runtime = AgentRuntime(
        config,
        credential,
        connector=connector,
        retry_policy=FakeRetryPolicy(),
    )
    states = [runtime.state]
    set_state = runtime._set_state

    def record(state):
        set_state(state)
        states.append(state)

    runtime._set_state = record

    runtime.run()

    assert states == [
        AgentState.STARTING,
        AgentState.CONNECTING,
        AgentState.ONLINE,
        AgentState.BACKOFF,
        AgentState.CONNECTING,
        AgentState.STOPPED,
    ]


def test_lifecycle_events_are_exact_ordered_and_sanitized(config, credential):
    events = []
    conn = FakeSocket([b""])
    connector = ScriptedConnector(
        conn,
        OSError("controller.test credential payload PING AUTH_REJECT"),
        AuthRejected("secret exception text"),
    )
    runtime = AgentRuntime(
        config,
        credential,
        connector=connector,
        retry_policy=FakeRetryPolicy(),
        event_sink=events.append,
    )

    runtime.run()

    assert events == [
        {"event": "PROCESS_START", "state": "STARTING", "attempt": 0},
        {"event": "STATE_TRANSITION", "state": "CONNECTING", "attempt": 1},
        {"event": "CONNECTION_ATTEMPT", "state": "CONNECTING", "attempt": 1},
        {"event": "AUTH_ACCEPTED", "state": "CONNECTING", "attempt": 1},
        {"event": "CONNECTION_SUCCESS", "state": "CONNECTING", "attempt": 1},
        {"event": "STATE_TRANSITION", "state": "ONLINE", "attempt": 1},
        {
            "event": "CONNECTION_FAILURE",
            "state": "ONLINE",
            "attempt": 1,
            "category": "network",
        },
        {
            "event": "SOCKET_CLOSE",
            "state": "ONLINE",
            "attempt": 1,
            "category": "clean",
        },
        {"event": "STATE_TRANSITION", "state": "BACKOFF", "attempt": 1},
        {"event": "RETRY_DELAY", "state": "BACKOFF", "attempt": 1, "delay": 0},
        {"event": "STATE_TRANSITION", "state": "CONNECTING", "attempt": 2},
        {"event": "CONNECTION_ATTEMPT", "state": "CONNECTING", "attempt": 2},
        {
            "event": "CONNECTION_FAILURE",
            "state": "CONNECTING",
            "attempt": 2,
            "category": "network",
        },
        {"event": "STATE_TRANSITION", "state": "BACKOFF", "attempt": 2},
        {"event": "RETRY_DELAY", "state": "BACKOFF", "attempt": 2, "delay": 0},
        {"event": "STATE_TRANSITION", "state": "CONNECTING", "attempt": 3},
        {"event": "CONNECTION_ATTEMPT", "state": "CONNECTING", "attempt": 3},
        {"event": "AUTH_REJECTED", "state": "CONNECTING", "attempt": 3},
        {
            "event": "CONNECTION_FAILURE",
            "state": "CONNECTING",
            "attempt": 3,
            "category": "auth",
        },
        {"event": "STATE_TRANSITION", "state": "STOPPED", "attempt": 3},
        {"event": "PROCESS_STOP", "state": "STOPPED", "attempt": 3},
    ]
    serialized = json.dumps(events)
    for sensitive in (
        config.controller_host,
        credential.agent_id,
        credential.key_id,
        "credential",
        "payload",
        "PING",
        "secret exception text",
    ):
        assert sensitive not in serialized


def test_production_auth_reject_emits_cause_before_single_close(config, credential):
    events = []
    connector, _, tls, _, _, _ = make_connector(
        config, credential, result="AUTH_REJECT"
    )
    runtime = AgentRuntime(
        config, credential, connector=connector, event_sink=events.append
    )

    runtime.run()

    relevant = [
        event
        for event in events
        if event["event"] in {"AUTH_REJECTED", "CONNECTION_FAILURE", "SOCKET_CLOSE"}
    ]
    assert relevant == [
        {"event": "AUTH_REJECTED", "state": "CONNECTING", "attempt": 1},
        {
            "event": "CONNECTION_FAILURE",
            "state": "CONNECTING",
            "attempt": 1,
            "category": "auth",
        },
        {
            "event": "SOCKET_CLOSE",
            "state": "CONNECTING",
            "attempt": 1,
            "category": "clean",
        },
    ]
    assert tls.closed


def test_protocol_and_heartbeat_failures_emit_safe_categories(config, credential):
    events = []
    protocol_conn = FakeSocket([frame(b"unexpected")])
    heartbeat_conn = FakeSocket([TimeoutError()])
    connector = ScriptedConnector(protocol_conn, heartbeat_conn, AuthRejected())
    ticks = iter([0, 3, 6])
    runtime = AgentRuntime(
        config,
        credential,
        connector=connector,
        retry_policy=FakeRetryPolicy(),
        clock=lambda: next(ticks),
        event_sink=events.append,
    )

    runtime.run()

    categories = [
        event["category"] for event in events if event["event"] == "CONNECTION_FAILURE"
    ]
    assert categories == ["protocol", "heartbeat", "auth"]
    assert [event for event in events if event["event"] == "HEARTBEAT_DEADLINE"] == [
        {"event": "HEARTBEAT_DEADLINE", "state": "ONLINE", "attempt": 2}
    ]


def test_failure_categories_use_exception_classes_not_text(config, credential):
    events = []
    connector = ScriptedConnector(
        TimeoutError("tls auth protocol secret"),
        ssl.SSLError("network timeout secret"),
        AuthRejected(),
    )
    runtime = AgentRuntime(
        config,
        credential,
        connector=connector,
        retry_policy=FakeRetryPolicy(),
        event_sink=events.append,
    )

    runtime.run()

    assert [
        event["category"] for event in events if event["event"] == "CONNECTION_FAILURE"
    ] == ["timeout", "tls", "auth"]
    assert "secret" not in json.dumps(events)


def test_event_sink_failure_never_kills_owner_loop(config, credential):
    connector = ScriptedConnector(AuthRejected())

    def broken_sink(_event):
        raise RuntimeError("logging unavailable")

    runtime = AgentRuntime(
        config, credential, connector=connector, event_sink=broken_sink
    )

    runtime.run()

    assert connector.calls == 1
    assert runtime.state is AgentState.STOPPED


def test_event_sink_is_reentrant_and_called_outside_state_lock(config, credential):
    events = []
    conn = FakeSocket()
    connector = ScriptedConnector(conn)
    runtime = None

    def sink(event):
        events.append(event)
        if event == {
            "event": "STATE_TRANSITION",
            "state": "ONLINE",
            "attempt": 1,
        }:
            assert runtime.state is AgentState.ONLINE
            runtime.stop()

    runtime = AgentRuntime(config, credential, connector=connector, event_sink=sink)
    thread = threading.Thread(target=runtime.run, name="runtime-reentrant-sink")
    thread.start()
    thread.join(1)
    if thread.is_alive():
        runtime.stop()
        thread.join(1)

    assert not thread.is_alive()
    assert runtime.state is AgentState.STOPPED
    assert events[-1] == {"event": "PROCESS_STOP", "state": "STOPPED", "attempt": 1}


@pytest.mark.parametrize("stop_event", ["STATE_TRANSITION", "CONNECTION_ATTEMPT"])
def test_reentrant_sink_stop_before_dial_skips_connection(
    config, credential, stop_event
):
    events = []
    connector = ScriptedConnector()
    runtime = None

    def sink(event):
        events.append(event)
        if event["event"] == stop_event:
            runtime.stop()

    runtime = AgentRuntime(config, credential, connector=connector, event_sink=sink)

    runtime.run()

    assert connector.calls == 0
    assert runtime.state is AgentState.STOPPED
    assert events[-1] == {"event": "PROCESS_STOP", "state": "STOPPED", "attempt": 1}


def test_close_error_does_not_abort_transient_retry(config, credential):
    class CloseErrorSocket(FakeSocket):
        def close(self):
            super().close()
            raise OSError("close failed")

    conn = CloseErrorSocket([b""])
    connector = ScriptedConnector(conn, AuthRejected())
    retry = FakeRetryPolicy()
    runtime = AgentRuntime(config, credential, connector=connector, retry_policy=retry)

    runtime.run()

    assert conn.closed
    assert connector.calls == 2
    assert retry.next_calls == 1


def test_unexpected_online_frame_retries_instead_of_becoming_fatal(config, credential):
    conn = FakeSocket([frame(b"unexpected")])
    connector = ScriptedConnector(conn, AuthRejected())
    retry = FakeRetryPolicy()
    runtime = AgentRuntime(config, credential, connector=connector, retry_policy=retry)

    runtime.run()

    assert connector.calls == 2
    assert retry.next_calls == 1
    assert conn.closed


def test_stop_interrupts_backoff_without_second_attempt(config, credential):
    entered_backoff = threading.Event()
    connector = ScriptedConnector(OSError("offline"))
    retry = FakeRetryPolicy(delay=60)
    retry.on_next = entered_backoff.set
    runtime = AgentRuntime(config, credential, connector=connector, retry_policy=retry)
    thread = threading.Thread(target=runtime.run, name="runtime-test")
    thread.start()
    try:
        assert entered_backoff.wait(1)
        assert runtime.state is AgentState.BACKOFF
        runtime.stop()
        thread.join(1)
    finally:
        runtime.stop()
        thread.join(1)

    assert not thread.is_alive()
    assert connector.calls == 1
    assert runtime.state is AgentState.STOPPED


class BlockingOnlineSocket(FakeSocket):
    def __init__(self, operation, entered, released):
        super().__init__([frame(b"PING")])
        self.operation = operation
        self.entered = entered
        self.released = released

    def recv(self, size):
        if self.operation == "recv":
            self.entered.set()
            assert self.released.wait(1)
            raise OSError("recv interrupted")
        return super().recv(size)

    def sendall(self, packet):
        if self.operation == "send":
            self.entered.set()
            assert self.released.wait(1)
            raise OSError("send interrupted")
        super().sendall(packet)

    def shutdown(self, how):
        super().shutdown(how)
        self.released.set()


@pytest.mark.parametrize("operation", ["recv", "send"])
def test_stop_interrupts_online_io_without_thread_leak(config, credential, operation):
    entered = threading.Event()
    released = threading.Event()
    events = []
    conn = BlockingOnlineSocket(operation, entered, released)
    connector = ScriptedConnector(conn)
    runtime = AgentRuntime(
        config, credential, connector=connector, event_sink=events.append
    )
    thread = threading.Thread(target=runtime.run, name=f"runtime-online-{operation}")
    thread.start()
    try:
        assert entered.wait(1)
        assert runtime.state is AgentState.ONLINE
        runtime.stop()
        thread.join(1)
    finally:
        released.set()
        runtime.stop()
        thread.join(1)

    assert not thread.is_alive()
    assert socket.SHUT_RDWR in conn.shutdown_calls
    assert conn.closed
    assert runtime.state is AgentState.STOPPED
    assert [event for event in events if event["event"] == "SOCKET_CLOSE"] == [
        {
            "event": "SOCKET_CLOSE",
            "state": "ONLINE",
            "attempt": 1,
            "category": "forced",
        }
    ]


class BlockingRaw(FakeRawSocket):
    def __init__(self, events, phase, entered, released):
        super().__init__(events)
        self.phase = phase
        self.entered = entered
        self.released = released

    def connect(self, address):
        super().connect(address)
        if self.phase == "connect":
            self.entered.set()
            assert self.released.wait(1)
            raise OSError("connect interrupted")

    def shutdown(self, how):
        super().shutdown(how)
        self.released.set()


class BlockingTls(FakeTlsSocket):
    def __init__(self, events, phase, entered, released):
        nonce = b"n" * 32
        super().__init__(
            [
                json_frame(
                    {"type": "CHALLENGE", "nonce": base64.b64encode(nonce).decode()}
                ),
                json_frame({"type": "AUTH_OK"}),
            ],
            events,
        )
        self.phase = phase
        self.entered = entered
        self.released = released

    def do_handshake(self):
        super().do_handshake()
        if self.phase == "tls":
            self.entered.set()
            assert self.released.wait(1)
            raise OSError("handshake interrupted")

    def recv(self, size):
        if self.phase == "auth":
            self.entered.set()
            assert self.released.wait(1)
            raise OSError("auth interrupted")
        return super().recv(size)

    def shutdown(self, how):
        super().shutdown(how)
        self.released.set()


@pytest.mark.parametrize("phase", ["connect", "tls", "auth"])
def test_stop_interrupts_every_connection_phase_without_thread_leak(
    config, credential, phase
):
    events = []
    entered = threading.Event()
    released = threading.Event()
    raw = BlockingRaw(events, phase, entered, released)
    tls = BlockingTls(events, phase, entered, released)
    connector = ManagedConnector(
        socket_factory=lambda *_: raw,
        context_factory=lambda: FakeTlsContext(tls, events),
    )
    runtime = AgentRuntime(config, credential, connector=connector)
    thread = threading.Thread(target=runtime.run, name=f"runtime-{phase}")
    thread.start()
    try:
        assert entered.wait(1)
        runtime.stop()
        runtime.stop()
        thread.join(1)
    finally:
        released.set()
        runtime.stop()
        thread.join(1)

    assert not thread.is_alive()
    assert runtime.state is AgentState.STOPPED
    interrupted = raw if phase == "connect" else tls
    assert socket.SHUT_RDWR in interrupted.shutdown_calls
    assert interrupted.closed


def test_stop_that_wins_socket_registration_race_prevents_blocking_connect(
    config, credential
):
    events = []
    raw = FakeRawSocket(events)
    created = threading.Event()
    release = threading.Event()

    def create(*_):
        created.set()
        assert release.wait(1)
        return raw

    connector = ManagedConnector(
        socket_factory=create,
        context_factory=lambda: pytest.fail("TLS should not start"),
    )
    runtime = AgentRuntime(config, credential, connector=connector)
    thread = threading.Thread(target=runtime.run, name="runtime-registration-race")
    thread.start()
    try:
        assert created.wait(1)
        runtime.stop()
        release.set()
        thread.join(1)
    finally:
        release.set()
        runtime.stop()
        thread.join(1)

    assert not thread.is_alive()
    assert raw.shutdown_calls == [socket.SHUT_RDWR]
    assert raw.closed
    assert not any(
        isinstance(event, tuple) and event[0] == "connect" for event in events
    )
    assert runtime.state is AgentState.STOPPED
