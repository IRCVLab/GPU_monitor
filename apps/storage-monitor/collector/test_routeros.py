import pytest

from collector.routeros import Connection, RouterOSError, encode_length, encode_sentence


class FakeSocket:
    def __init__(self, reply: bytes, chunk: int = 3) -> None:
        self.reply, self.chunk, self.sent, self.closed = bytearray(reply), chunk, b"", False

    def sendall(self, data):
        self.sent += data

    def recv(self, count):
        out, self.reply = bytes(self.reply[:min(count, self.chunk)]), self.reply[min(count, self.chunk):]
        return out

    def close(self):
        self.closed = True


def sentence(*words):
    return encode_sentence(words)


@pytest.mark.parametrize("length,encoded", [
    (0, b"\x00"), (0x7F, b"\x7f"), (0x80, b"\x80\x80"), (0x3FFF, b"\xbf\xff"), (0x4000, b"\xc0\x40\x00"),
    (0x1FFFFF, b"\xdf\xff\xff"), (0x200000, b"\xe0\x20\x00\x00"), (0x10000000, b"\xf0\x10\x00\x00\x00"),
])
def test_length_encoding_matches_the_wire_format_and_decodes_back(length, encoded):
    assert encode_length(length) == encoded
    assert Connection(FakeSocket(encoded + b"x" * 0))._read_length() == length


def test_word_lengths_round_trip_through_a_sentence_at_the_boundaries():
    for size in (0x7F, 0x80, 0x3FFF, 0x4000):
        word = "a" * size
        assert Connection(FakeSocket(sentence(word)))._read_sentence() == [word]


def test_rows_are_collected_until_done():
    reply = sentence("!re", "=name=bond_nas", "=rx-byte=10", "=tx-byte=20") + sentence("!re", "=name=x") + sentence("!done")
    sock = FakeSocket(reply)
    rows = Connection(sock).talk(["/interface/print", "=stats="])
    assert rows == [{"name": "bond_nas", "rx-byte": "10", "tx-byte": "20"}, {"name": "x"}]
    assert sock.sent == sentence("/interface/print", "=stats=")


def test_a_trap_raises_after_the_session_is_drained_and_hides_the_secret():
    reply = sentence("!trap", "=message=bad login hunter2 (6)") + sentence("!done")
    sock = FakeSocket(reply)
    with pytest.raises(RouterOSError) as error:
        Connection(sock).talk(["/login", "=password=hunter2"], secret="hunter2")
    assert "hunter2" not in str(error.value) and "bad login" in str(error.value)
    assert not sock.reply


def test_fatal_and_a_closed_connection_raise():
    with pytest.raises(RouterOSError, match="closed the session"):
        Connection(FakeSocket(sentence("!fatal", "session terminated"))).talk(["/x"])
    with pytest.raises(RouterOSError, match="connection closed"):
        Connection(FakeSocket(b"\x05ab")).talk(["/x"])


def test_an_oversized_word_is_refused():
    with pytest.raises(RouterOSError, match="longer than the limit"):
        Connection(FakeSocket(encode_length(2 << 20))).talk(["/x"])
