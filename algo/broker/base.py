from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: str
    symbol: str
    side: str
    quantity: int
    average_price: float
    raw: dict[str, Any]


class Broker(ABC):
    @abstractmethod
    def login(self) -> None: ...

    @abstractmethod
    def quote(self, symbol: str) -> float: ...

    def quotes(self, symbols: Iterable[str]) -> dict[str, float]:
        return {symbol: self.quote(symbol) for symbol in symbols}

    @abstractmethod
    def place_order(self, symbol: str, side: str, quantity: int, **kwargs: Any) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def positions(self) -> Any: ...

    @abstractmethod
    def funds(self) -> Any: ...

    def subscribe(self, symbols: Iterable[str], callback: Callable[[dict[str, Any]], None]) -> None:
        raise NotImplementedError
