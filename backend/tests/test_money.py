import pytest

from apps.common.money import allocate, percent_of


@pytest.mark.parametrize(
    ("amount", "bps", "expected"),
    [
        (100_000, 1800, 18_000),  # ₹1000 @ 18% = ₹180
        (0, 1800, 0),
        (100_000, 0, 0),
        (1, 1800, 0),  # 0.18 paise rounds down
        (3, 1800, 1),  # 0.54 paise rounds up
        (25, 2000, 5),  # exact
        (50, 100, 1),  # 0.5 paise: half rounds up
    ],
)
def test_percent_of(amount, bps, expected):
    assert percent_of(amount, bps) == expected


def test_percent_of_rejects_negative():
    with pytest.raises(ValueError):
        percent_of(-1, 1800)


@pytest.mark.parametrize(
    ("total", "weights"),
    [
        (100, [1, 1, 1]),
        (10_000, [200_000, 120_000, 60_000]),
        (1, [5, 5]),
        (0, [3, 4]),
        (999, [1]),
        (7, [0, 3, 0]),
    ],
)
def test_allocate_always_sums_to_total(total, weights):
    parts = allocate(total, weights)
    assert sum(parts) == total
    assert all(p >= 0 for p in parts)
    assert [p for p, w in zip(parts, weights, strict=True) if w == 0] == [0] * weights.count(0)


def test_allocate_is_proportional_with_largest_remainder():
    assert allocate(100, [1, 1, 1]) == [34, 33, 33]
    assert allocate(10, [3, 1]) == [8, 2]  # 7.5 / 2.5 -> tie broken by earlier line


def test_allocate_zero_weights():
    assert allocate(0, [0, 0]) == [0, 0]
    with pytest.raises(ValueError):
        allocate(5, [0, 0])
