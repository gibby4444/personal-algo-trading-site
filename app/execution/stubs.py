"""Robinhood and Webull are not wired. Official retail API support was not verified.

These classes exist so a later adapter can be dropped in beside Alpaca. Calling them
does not log in and does not place an order.
"""

from __future__ import annotations

from app.strategies.base import OrderReq


class _UnverifiedBroker:
    name = "unverified"
    mode = "paper"

    def submit(self, order: OrderReq) -> dict:
        raise NotImplementedError(
            f"{self.name} is a stub. Official API access was not verified, and this desk does not send orders there."
        )

    def cancel(self, order_id: str) -> None:
        raise NotImplementedError(f"{self.name} cancel is not implemented")

    def positions(self) -> list:
        raise NotImplementedError(f"{self.name} positions are not implemented")

    def account(self) -> dict:
        raise NotImplementedError(f"{self.name} account is not implemented")


class RobinhoodBroker(_UnverifiedBroker):
    name = "robinhood"


class WebullBroker(_UnverifiedBroker):
    name = "webull"
