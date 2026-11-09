"""LineAssembler: terminators, chunk-boundary independence (property test), garbage input."""
import pytest
from hypothesis import given, settings, strategies as st
from server import LineAssembler


def lines(chunks):
    a, out = LineAssembler(), []
    for c in chunks: out += [t for t, _, _ in a.feed(c)]
    return out + [t for t, _, _ in a.flush()]


@pytest.mark.parametrize("data,expected", [
    (b"A\nB\nC\n", ["A", "B", "C"]),
    (b"A\r\nB\r\nC\r\n", ["A", "B", "C"]),
    (b"A\rB\rC\r", ["A", "B", "C"]),                       # CR-only devices (old instruments)
    (b"A\n\rB\n\r", ["A", "B"]),                           # LFCR
    (b"A\nB\r\nC\rD\n\rE\n", ["A", "B", "C", "D", "E"]),   # mixed
    (b"\n\n\r\r\r\nA\n\n", ["A"]),                         # empty lines ignored
    (b"A\x00B\n", ["A\\x00B"]),
    (b"\xff\xfe\x80\n", ["\\xFF\\xFE\\x80"]),
    (b"no terminator", ["no terminator  [partial]"]),
])
def test_examples(data, expected):
    assert lines([data]) == expected


def test_long_line_is_split_not_dropped():
    out = lines([b"A" * 10000])
    assert [len(x.replace("  [partial]", "")) for x in out] == [4096, 4096, 1808] and all("[partial]" in x for x in out)


@settings(max_examples=400, deadline=None)
@given(st.binary(max_size=700), st.lists(st.integers(1, 60), min_size=1, max_size=40))
def test_chunking_never_changes_the_result(data, cuts):
    chunks, i = [], 0
    for c in cuts:
        chunks.append(data[i:i + c]); i += c
    chunks.append(data[i:])
    assert lines(chunks) == lines([data])


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=3000))
def test_arbitrary_bytes_never_crash_and_output_is_printable(data):
    for text in lines([data]):
        assert all(32 <= ord(c) < 127 for c in text)
        assert len(text) <= LineAssembler.MAXLEN * 4 + 20


def test_idle_flush_then_continuation():
    a = LineAssembler()
    assert a.feed(b"par") == [] and [t for t, _, _ in a.flush()] == ["par  [partial]"]
    assert [t for t, _, _ in a.feed(b"tial\n")] == ["tial"]
