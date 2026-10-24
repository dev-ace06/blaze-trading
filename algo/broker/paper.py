from __future__ import annotations

import itertools
from typing import Any, Callable

from .base import Broker, OrderResult


class PaperBroker(Broker):
    def __init__(
        self,
        initial_cash: float,
        quote_provider: Callable[[str], float],
        slippage: float = 0.0003,
    ) -> None:
        self.initial_cash = float(initial_cash)
        self.quote_provider = quote_provider
        self.slippage = float(slippage)
        self._positions: dict[str, int] = {}
        self._orders: dict[str, OrderResult] = {}
        self._ids = itertools.count(1)

    def login(self) -> None:
        return None

    def quote(self, symbol: str) -> float:
        price = float(self.quote_provider(symbol))
        if price <= 0:
            raise ValueError(f"invalid quote for {symbol}")
        return price

    def place_order(self, symbol: str, side: str, quantity: int, **kwargs: Any) -> OrderResult:
        if quantity < 1 or side not in {"BUY", "SELL", "B", "S"}:
            raise ValueError("quantity and side are invalid")
        normalized_side = "BUY" if side in {"BUY", "B"} else "SELL"
        ltp = self.quote(symbol)
        price = ltp * (1 + self.slippage if normalized_side == "BUY" else 1 - self.slippage)
        order_id = f"PAPER-{next(self._ids)}"
        signed_qty = quantity if normalized_side == "BUY" else -quantity
        self._positions[symbol] = self._positions.get(symbol, 0) + signed_qty
        if not self._positions[symbol]:
            del self._positions[symbol]
        result = OrderResult(order_id, "FILLED", symbol, normalized_side, quantity, price, {"mode": "paper"})
        self._orders[order_id] = result
        return result

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        order = self._orders.get(order_id)
        return {"status": "already_filled" if order else "not_found", "order_id": order_id}

    def positions(self) -> dict[str, int]:
        return dict(self._positions)

    def funds(self) -> dict[str, float]:
        return {"cash": self.initial_cash}

    def flatten(self) -> list[OrderResult]:
        results = []
        for symbol, quantity in list(self._positions.items()):
            side = "SELL" if quantity > 0 else "BUY"
            results.append(self.place_order(symbol, side, abs(quantity)))
        return results
