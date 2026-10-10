"""Pricing rules."""

TAX_RATE = 0.2


def apply_discount(amount, percent):
    """Return amount after a percentage discount."""
    return round(amount * (1 - percent / 100), 2)


def with_tax(amount):
    return apply_discount(amount, 0) * (1 + TAX_RATE)
