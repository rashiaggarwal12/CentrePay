"""Integer-paise arithmetic. Money never touches a float."""


def percent_of(amount_paise: int, rate_bps: int) -> int:
    """amount * rate (basis points), rounded half-up to the nearest paisa.

    1800 bps = 18%. Using integers only avoids float drift (0.1 + 0.2 != 0.3).
    """
    if amount_paise < 0 or rate_bps < 0:
        raise ValueError("amount and rate must be non-negative")
    return (amount_paise * rate_bps + 5_000) // 10_000


def allocate(total: int, weights: list[int]) -> list[int]:
    """Split `total` across `weights` proportionally, so the parts sum to exactly `total`.

    Uses the largest-remainder method: floor every share, then hand the leftover
    paise to the shares with the biggest remainders (ties go to the earlier line).
    """
    if total < 0 or any(w < 0 for w in weights):
        raise ValueError("total and weights must be non-negative")
    weight_sum = sum(weights)
    if weight_sum == 0:
        if total:
            raise ValueError("cannot allocate a non-zero total across zero weights")
        return [0] * len(weights)

    shares = [total * w // weight_sum for w in weights]
    remainders = [total * w % weight_sum for w in weights]
    leftover = total - sum(shares)
    by_remainder = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for i in by_remainder[:leftover]:
        shares[i] += 1
    return shares
