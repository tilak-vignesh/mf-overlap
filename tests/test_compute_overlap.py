import uuid

from app.tools.compute_overlap import compute_overlap


def test_no_common_stocks():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert compute_overlap({a: 0.5}, {b: 0.5}) == 0


def test_full_overlap():
    s = uuid.uuid4()
    assert compute_overlap({s: 0.3}, {s: 0.6}) == 0.3


def test_partial_overlap():
    s1, s2, s3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    holdings_a = {s1: 0.2, s2: 0.3}
    holdings_b = {s2: 0.1, s3: 0.4}
    assert compute_overlap(holdings_a, holdings_b) == 0.1
