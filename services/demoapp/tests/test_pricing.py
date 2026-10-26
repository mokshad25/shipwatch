from decimal import Decimal

import pytest
from demoapp import pricing


def test_subtotal_sums_price_times_quantity():
    assert pricing.subtotal([(Decimal("24.90"), 2), (Decimal("4.20"), 1)]) == Decimal("54.00")


@pytest.mark.parametrize(
    ("code", "discount"),
    [
        (None, Decimal("0.00")),
        ("WELCOME10", Decimal("10.00")),
        ("vip25 ", Decimal("25.00")),  # case and surrounding spaces don't matter
    ],
)
def test_apply_discount(code, discount):
    assert pricing.apply_discount(Decimal("100.00"), code) == discount


def test_discount_rounds_half_up_to_cents():
    assert pricing.apply_discount(Decimal("0.05"), "WELCOME10") == Decimal("0.01")


def test_unknown_code_raises():
    with pytest.raises(pricing.UnknownDiscountCode):
        pricing.apply_discount(Decimal("10"), "SAVE99")
