"""Checkout service."""

import json
import logging as log

import requests

from . import pricing
from .models import Order, Product as P

logger = log.getLogger(__name__)


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def build_order(path):
    rows = _load(path)
    products = [P(id=r["id"], name=r["name"], price=r["price"]) for r in rows]
    return Order(1, products)


def checkout(path, discount=0):
    order = build_order(path)

    def notify(total):
        requests.post("https://example.com/hook", json={"total": total})
        logger.info("notified")

    total = pricing.apply_discount(order.total(), discount)
    notify(total)
    return total


class Retrying:
    def __init__(self, attempts=3):
        self.attempts = attempts

    def run(self, fn):
        for _ in range(self.attempts):
            try:
                return fn()
            except requests.RequestException:
                continue
        return self.fallback()

    def fallback(self):
        return None
