"""Order pricing: subtotal and discount codes."""

from decimal import ROUND_HALF_UP, Decimal

# Percent off the subtotal.
DISCOUNT_CODES: dict[str, Decimal] = {
    "WELCOME10": Decimal("10"),
    "SPRING15": Decimal("15"),
    "VIP25": Decimal("25"),
}

CENT = Decimal("0.01")


class UnknownDiscountCode(ValueError):
    pass


def subtotal(lines: list[tuple[Decimal, int]]) -> Decimal:
    """Sum of unit price x quantity, rounded to cents."""
    return sum((price * qty for price, qty in lines), Decimal(0)).quantize(CENT)


def discount_rate(code: str | None) -> Decimal:
    """Percent off for `code`; 0 when there is no code. Raises UnknownDiscountCode."""
    if code is None:
        return Decimal(0)
    rate = DISCOUNT_CODES.get(code.strip().upper())
    if rate is None:
        raise UnknownDiscountCode(code)
    return rate


def apply_discount(amount: Decimal, code: str | None) -> Decimal:
    """The discount (not the new total) for `code` on `amount`, rounded to cents."""
    return (amount * discount_rate(code) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
