"""Property tests de la reconexión sin duplicados (§4.1/§4.4)."""

from hypothesis import given
from hypothesis import strategies as st

from panel.core.stream import SeqDedup, backlog_seqs


@given(
    persisted=st.lists(st.integers(min_value=1, max_value=1000), max_size=200),
    last_seq=st.integers(min_value=0, max_value=1000),
)
def test_backlog_is_sorted_unique_and_above_last_seq(persisted, last_seq):
    out = backlog_seqs(persisted, last_seq)
    assert out == sorted(out)
    assert len(out) == len(set(out))
    assert all(s > last_seq for s in out)
    # completo: cubre exactamente los seqs persistidos > last_seq
    assert set(out) == {s for s in persisted if s > last_seq}


@given(
    last_seq=st.integers(min_value=0, max_value=100),
    # backlog: seqs > last_seq ya emitidos; live: llegada arbitraria (con solapes)
    live=st.lists(st.integers(min_value=1, max_value=200), max_size=300),
)
def test_no_duplicates_across_backlog_then_live(last_seq, live):
    # El backlog emite todo lo persistido hasta cierto punto; simulamos que el
    # backlog cubrió [last_seq+1 .. hi]. Luego el live llega con posibles dups.
    hi = last_seq + 50
    backlog = list(range(last_seq + 1, hi + 1))
    dedup = SeqDedup(last_seq)
    emitted = []
    for s in backlog:
        assert dedup.should_forward(s)  # backlog siempre monótono creciente
        emitted.append(s)
    for s in live:
        if dedup.should_forward(s):
            emitted.append(s)
    # Invariante: nunca se emite dos veces el mismo seq, y es estrictamente creciente.
    assert len(emitted) == len(set(emitted))
    assert emitted == sorted(emitted)
    assert all(s > last_seq for s in emitted)


def test_seqdedup_rejects_already_seen():
    d = SeqDedup(5)
    assert not d.should_forward(5)
    assert not d.should_forward(3)
    assert d.should_forward(6)
    assert not d.should_forward(6)
    assert d.should_forward(7)
