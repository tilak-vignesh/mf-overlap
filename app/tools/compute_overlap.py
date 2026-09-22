import uuid


def compute_overlap(
    holdings_a: dict[uuid.UUID, float], holdings_b: dict[uuid.UUID, float]
) -> float:
    """sum(min(weight_i, weight_j)) over stocks common to both funds."""
    common = holdings_a.keys() & holdings_b.keys()
    return sum(min(holdings_a[s], holdings_b[s]) for s in common)
