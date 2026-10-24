from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class CostModel:
    brokerage_per_order: float = 0.0
    stt_sell: float = 0.00025
    exchange_txn: float = 0.0000297
    sebi_per_crore: float = 10.0
    stamp_buy: float = 0.00003
    gst: float = 0.18
    slippage: float = 0.0003

    def round_trip_charges(self, buy_price: float, sell_price: float, qty: int) -> float:
        buy_value = buy_price * qty
        sell_value = sell_price * qty
        turnover = buy_value + sell_value
        brokerage = 2 * self.brokerage_per_order
        stt = sell_value * self.stt_sell
        exchange = turnover * self.exchange_txn
        sebi = turnover * self.sebi_per_crore / 10_000_000
        stamp = buy_value * self.stamp_buy
        gst = self.gst * (brokerage + exchange + sebi)
        return brokerage + stt + exchange + sebi + stamp + gst


@dataclass
class Position:
    symbol: str
    side: str
    qty: int
    entry_price: float
    initial_qty: int
    initial_stop: float
    stop: float
    reason: str
    opened_at: datetime
    scaled_out: bool = False


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: str
    qty: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    costs: float
    net_pnl: float
    r_multiple: float
    reason: str
    opened_at: datetime
    closed_at: datetime


class Portfolio:
    def __init__(self, initial_equity: float, costs: CostModel | None = None) -> None:
        self.initial_equity = float(initial_equity)
        self.costs = costs or CostModel()
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.realized_pnl = 0.0

    @property
    def equity(self) -> float:
        return self.initial_equity + self.realized_pnl

    def open_position(
        self,
        symbol: str,
        side: str,
        qty: int,
        ltp: float,
        stop: float,
        reason: str,
        at: datetime | None = None,
    ) -> Position:
        if qty < 1 or symbol in self.positions:
            raise ValueError("invalid or duplicate position")
        factor = 1 + self.costs.slippage if side == "LONG" else 1 - self.costs.slippage
        fill = ltp * factor
        position = Position(symbol, side, qty, fill, qty, stop, stop, reason, at or datetime.now(timezone.utc))
        self.positions[symbol] = position
        return position

    def close_position(
        self,
        symbol: str,
        ltp: float,
        reason: str,
        qty: int | None = None,
        at: datetime | None = None,
    ) -> Trade:
        position = self.positions[symbol]
        close_qty = min(position.qty, qty or position.qty)
        factor = 1 - self.costs.slippage if position.side == "LONG" else 1 + self.costs.slippage
        exit_price = ltp * factor
        sign = 1 if position.side == "LONG" else -1
        gross = (exit_price - position.entry_price) * close_qty * sign
        buy_price = position.entry_price if position.side == "LONG" else exit_price
        sell_price = exit_price if position.side == "LONG" else position.entry_price
        charges = self.costs.round_trip_charges(buy_price, sell_price, close_qty)
        net = gross - charges
        risk_per_share = abs(position.entry_price - position.initial_stop)
        r_multiple = net / (risk_per_share * close_qty) if risk_per_share and close_qty else 0.0
        trade = Trade(
            symbol,
            position.side,
            close_qty,
            position.entry_price,
            exit_price,
            gross,
            charges,
            net,
            r_multiple,
            reason,
            position.opened_at,
            at or datetime.now(timezone.utc),
        )
        self.trades.append(trade)
        self.realized_pnl += net
        position.qty -= close_qty
        if position.qty == 0:
            del self.positions[symbol]
        else:
            position.scaled_out = True
        return trade

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(
            (prices.get(symbol, position.entry_price) - position.entry_price)
            * position.qty
            * (1 if position.side == "LONG" else -1)
            for symbol, position in self.positions.items()
        )

    def gross_exposure(self, prices: dict[str, float]) -> float:
        return sum(abs(prices.get(symbol, position.entry_price) * position.qty) for symbol, position in self.positions.items())
