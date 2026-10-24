from __future__ import annotations

import asyncio
import io
import threading
import time
from typing import Any, Callable, Iterable

import pyotp

from .base import Broker, OrderResult

NO_DATA_CODES = {"5203"}


def _has_error(response: Any) -> bool:
    if not isinstance(response, dict):
        return True
    if any(key in response for key in ("error", "Error", "Error Message")):
        return True
    if str(response.get("stat", "ok")).lower() not in {"ok", "success"}:
        return True
    data = response.get("data")
    status = data.get("status") if isinstance(data, dict) else None
    return status is not None and str(status).lower() not in {"success", "ok"}


def _error_text(response: Any) -> str:
    if not isinstance(response, dict):
        return "no response"
    for key in ("errMsg", "emsg", "Error Message", "message", "Error", "error"):
        value = response.get(key)
        if value:
            if isinstance(value, list) and value and isinstance(value[0], dict):
                value = value[0].get("message", value[0])
            return str(value)[:160]
    return f"stat={response.get('stat')}"


def _order_id(response: dict[str, Any]) -> str:
    data = response.get("data", response)
    if isinstance(data, dict):
        return str(data.get("nOrdNo") or response.get("nOrdNo") or data.get("orderId") or data.get("order_id") or "")
    return str(response.get("nOrdNo") or "")


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _rows(response: Any) -> list[dict[str, Any]]:
    data = response.get("data", response) if isinstance(response, dict) else response
    if isinstance(data, dict):
        data = [data]
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def normalise_mobile(value: str) -> str:
    digits = value.strip().replace(" ", "")
    if digits.startswith("+"):
        return digits
    if digits.startswith("91") and len(digits) == 12:
        return f"+{digits}"
    return f"+91{digits}"


class KotakNeoBroker(Broker):
    """Adapter around the installed kotakneoapi 3.x SDK. Every SDK call lives in this file.

    Callers use plain symbols (RELIANCE); the adapter maps them to Kotak trading symbols (RELIANCE-EQ)
    and instrument tokens once `load_instruments` has run. Unmapped names pass straight through.
    """

    def __init__(
        self,
        credentials: dict[str, str] | None = None,
        client: Any = None,
        environment: str = "prod",
        timeout: float | None = 10,
        product: str = "MIS",
        exchange_segment: str = "nse_cm",
        order_type: str = "MKT",
        validity: str = "DAY",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials or {}
        if client is None:
            from neo_api_client import NeoAPI

            key = self.credentials.get("KOTAK_CONSUMER_KEY")
            if not key:
                raise ValueError("KOTAK_CONSUMER_KEY is required for live mode")
            client = NeoAPI(consumer_key=key, environment=environment, timeout=timeout)
        self.client = client
        self.product = product
        self.exchange_segment = exchange_segment
        self.order_type = order_type
        self.validity = validity
        self.sleep = sleep
        self.tokens: dict[str, str] = {}
        self.trading_symbols: dict[str, str] = {}
        self.feed_error: str | None = None
        self._logged_in = False
        self._feed_stop = threading.Event()
        self._feed_thread: threading.Thread | None = None

    # ---- session -----------------------------------------------------------------------------

    def login(self) -> None:
        if self._logged_in:
            return
        required = ["KOTAK_MOBILE", "KOTAK_UCC", "KOTAK_MPIN", "KOTAK_TOTP_SECRET"]
        missing = [name for name in required if not self.credentials.get(name)]
        if missing:
            raise ValueError(f"missing Kotak credentials: {', '.join(missing)}")
        otp = pyotp.TOTP(self.credentials["KOTAK_TOTP_SECRET"].replace(" ", "")).now()
        first = self.client.totp_login(
            mobile_number=normalise_mobile(self.credentials["KOTAK_MOBILE"]),
            ucc=self.credentials["KOTAK_UCC"],
            totp=otp,
        )
        if _has_error(first):
            raise RuntimeError(f"Kotak TOTP login failed: {_error_text(first)}")
        second = self.client.totp_validate(mpin=self.credentials["KOTAK_MPIN"])
        if _has_error(second):
            raise RuntimeError(f"Kotak MPIN validation failed: {_error_text(second)}")
        self._logged_in = True

    # ---- instruments -------------------------------------------------------------------------

    def _scrip_master(self):
        import pandas as pd
        import requests

        location = self.client.scrip_master(exchange_segment=self.exchange_segment)
        if not isinstance(location, str):
            return None
        response = requests.get(location, timeout=30)
        response.raise_for_status()
        frame = pd.read_csv(io.BytesIO(response.content), low_memory=False)
        frame.columns = [str(column).strip().rstrip(";") for column in frame.columns]
        return frame

    def _resolve_bulk(self, symbols: list[str]) -> None:
        frame = self._scrip_master()
        if frame is None or "pSymbol" not in frame or "pTrdSymbol" not in frame:
            raise RuntimeError("scrip master unavailable")
        if "pGroup" in frame:
            equity = frame[frame["pGroup"].astype(str).str.strip().str.upper() == "EQ"]
            frame = equity if not equity.empty else frame
        names = frame["pSymbolName"].astype(str).str.strip().str.upper() if "pSymbolName" in frame else None
        trading = frame["pTrdSymbol"].astype(str).str.strip().str.upper()
        for symbol in symbols:
            wanted = symbol.upper()
            mask = trading == f"{wanted}-EQ"
            if names is not None and not mask.any():
                mask = names == wanted
            if mask.any():
                row = frame[mask].iloc[0]
                self.tokens[symbol] = str(int(float(row["pSymbol"])))
                self.trading_symbols[symbol] = str(row["pTrdSymbol"]).strip()

    def _resolve_search(self, symbols: list[str]) -> None:
        for symbol in symbols:
            response = self.client.search_scrip(exchange_segment=self.exchange_segment, symbol=symbol)
            for row in response if isinstance(response, list) else _rows(response):
                trading = str(row.get("pTrdSymbol") or row.get("trading_symbol") or "").strip()
                token = row.get("pSymbol") or row.get("instrument_token") or row.get("token")
                if token and (trading.upper() == f"{symbol.upper()}-EQ" or str(row.get("pSymbolName", "")).strip().upper() == symbol.upper()):
                    self.tokens[symbol] = str(int(float(token)))
                    self.trading_symbols[symbol] = trading or f"{symbol}-EQ"
                    break

    def load_instruments(self, symbols: Iterable[str]) -> dict[str, str]:
        pending = [symbol for symbol in symbols if symbol not in self.tokens]
        if pending:
            try:
                self._resolve_bulk(pending)
            except Exception:
                self._resolve_search([symbol for symbol in pending if symbol not in self.tokens])
        return dict(self.tokens)

    def _trading_symbol(self, symbol: str) -> str:
        return self.trading_symbols.get(symbol, symbol)

    def _plain_symbol(self, trading_symbol: str) -> str:
        for symbol, mapped in self.trading_symbols.items():
            if mapped.upper() == trading_symbol.upper():
                return symbol
        return trading_symbol[:-3] if trading_symbol.upper().endswith("-EQ") else trading_symbol

    # ---- market data -------------------------------------------------------------------------

    def quote(self, symbol: str) -> float:
        token = self.tokens.get(symbol, symbol)
        response = self.client.quotes(
            instrument_tokens=[{"instrument_token": str(token), "exchange_segment": self.exchange_segment}],
            quote_type="ltp",
        )
        rows = _rows(response)
        if not rows:
            raise RuntimeError(f"quote unavailable for {symbol}")
        for key in ("ltp", "last_traded_price", "lastPrice"):
            if key in rows[0]:
                return float(rows[0][key])
        raise RuntimeError(f"LTP missing for {symbol}")

    # ---- orders ------------------------------------------------------------------------------

    def _await_fill(self, order_id: str, attempts: int = 4) -> tuple[str, float]:
        """Best-effort status of a market order: ('FILLED', avg_price) or ('SUBMITTED', 0)."""
        for _ in range(attempts):
            try:
                rows = _rows(self.client.order_report(order_id=order_id))
            except Exception:
                return "SUBMITTED", 0.0
            row = rows[0] if rows else {}
            status = str(row.get("ordSt") or row.get("stat") or "").lower()
            if "reject" in status or "cancel" in status:
                raise RuntimeError(f"Kotak order {status}: {str(row.get('rejRsn') or '')[:120]}")
            average = _number(row.get("avgPrc"))
            if status in {"complete", "completed", "traded", "filled"} and average > 0:
                return "FILLED", average
            self.sleep(0.5)
        return "SUBMITTED", 0.0

    def _submit(self, symbol: str, side: str, quantity: int, order_type: str, trigger: str) -> dict[str, Any]:
        return self.client.place_order(
            exchange_segment=self.exchange_segment,
            product=self.product,
            price="0",
            order_type=order_type,
            quantity=str(quantity),
            validity=self.validity,
            trading_symbol=self._trading_symbol(symbol),
            transaction_type="B" if side in {"BUY", "B"} else "S",
            trigger_price=trigger,
        )

    def place_order(self, symbol: str, side: str, quantity: int, **kwargs: Any) -> OrderResult:
        if quantity < 1:
            raise ValueError("quantity must be positive")
        normalized_side = "BUY" if side in {"BUY", "B"} else "SELL"
        response = self._submit(symbol, normalized_side, quantity, self.order_type, "0")
        order_id = _order_id(response) if isinstance(response, dict) else ""
        if _has_error(response) or not order_id:
            raise RuntimeError(f"Kotak order rejected: {_error_text(response)}")
        status, average = self._await_fill(order_id)
        return OrderResult(order_id, status, symbol, normalized_side, quantity, average, response)

    def place_stop(self, symbol: str, side: str, quantity: int, trigger_price: float) -> OrderResult:
        normalized_side = "BUY" if side in {"BUY", "B"} else "SELL"
        response = self._submit(symbol, normalized_side, quantity, "SL-M", str(round(trigger_price, 2)))
        order_id = _order_id(response) if isinstance(response, dict) else ""
        if _has_error(response) or not order_id:
            raise RuntimeError(f"Kotak stop order rejected: {_error_text(response)}")
        return OrderResult(order_id, "SUBMITTED", symbol, normalized_side, quantity, 0.0, response)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.client.cancel_order(order_id=str(order_id), amo="NO")

    # ---- account -----------------------------------------------------------------------------

    def positions(self) -> dict[str, int]:
        """Net open quantity per plain symbol (negative = short). Raises when the book cannot be read."""
        response = self.client.positions()
        if not isinstance(response, dict):
            raise RuntimeError("Kotak positions unavailable")
        code = str(response.get("stCode", ""))
        message = _error_text(response).lower()
        if code in NO_DATA_CODES or "no data" in message or "no record" in message:
            return {}
        if _has_error(response):
            raise RuntimeError(f"Kotak positions error: {_error_text(response)}")
        net: dict[str, int] = {}
        for row in _rows(response):
            product = str(row.get("prod") or row.get("product") or "").upper()
            if product and product not in {"MIS", "INTRADAY"}:
                continue
            if str(row.get("exSeg") or self.exchange_segment).lower() != self.exchange_segment:
                continue
            quantity = int(
                _number(row.get("cfBuyQty")) + _number(row.get("flBuyQty")) - _number(row.get("cfSellQty")) - _number(row.get("flSellQty"))
            )
            trading = str(row.get("trdSym") or row.get("tradingSymbol") or "")
            if quantity and trading:
                symbol = self._plain_symbol(trading)
                net[symbol] = net.get(symbol, 0) + quantity
        return net

    def funds(self) -> dict[str, Any]:
        return self.client.limits()

    # ---- streaming ---------------------------------------------------------------------------

    def _normalise_message(self, message: Any, by_token: dict[str, str], indices: dict[str, str]) -> dict[str, Any] | None:
        kind = getattr(message, "type", "")
        if kind == "index":
            name = str(getattr(message, "name", "")).strip().lower()
            symbol = next((key for key, label in indices.items() if label.lower() == name), None)
            if symbol is None and len(indices) == 1:
                symbol = next(iter(indices))
            volume = None
        elif kind in {"scrip", "scrip_lite"}:
            symbol = by_token.get(str(getattr(message, "instrument_token", "")))
            volume = getattr(message, "volume_traded_today", None)
        else:
            return None
        price = getattr(message, "last_traded_price", 0)
        if not symbol or not price:
            return None
        return {"symbol": symbol, "ltp": float(price), "volume": volume, "timestamp": None}

    async def stream(
        self,
        symbols: Iterable[str],
        callback: Callable[[dict[str, Any]], None],
        indices: dict[str, str] | None = None,
    ) -> None:
        from neo_api_client.websocket.feed import WsToken

        indices = indices or {}
        by_token = {token: symbol for symbol, token in self.tokens.items()}
        scrips = [WsToken(self.exchange_segment, self.tokens[symbol]) for symbol in symbols if symbol in self.tokens]
        async with self.client.create_websocket() as websocket:
            if scrips:
                await websocket.subscribe_scrips(scrips)
            if indices:
                await websocket.subscribe_index([WsToken(self.exchange_segment, label) for label in indices.values()])
            async for message in websocket:
                if self._feed_stop.is_set():
                    return
                payload = self._normalise_message(message, by_token, indices)
                if payload:
                    callback(payload)

    def _feed_loop(self, symbols: list[str], callback: Callable[[dict[str, Any]], None], indices: dict[str, str]) -> None:
        delay = 2.0
        while not self._feed_stop.is_set():
            started = time.monotonic()
            try:
                asyncio.run(self.stream(symbols, callback, indices))
                self.feed_error = None
            except Exception as exc:
                self.feed_error = type(exc).__name__
            if time.monotonic() - started > 60:
                delay = 2.0
            self._feed_stop.wait(delay)
            delay = min(delay * 2, 30.0)

    def subscribe(
        self,
        symbols: Iterable[str],
        callback: Callable[[dict[str, Any]], None],
        indices: dict[str, str] | None = None,
    ) -> None:
        if self._feed_thread and self._feed_thread.is_alive():
            return
        self._feed_stop.clear()
        self._feed_thread = threading.Thread(
            target=self._feed_loop, args=(list(symbols), callback, dict(indices or {})), name="kotak-feed", daemon=True
        )
        self._feed_thread.start()

    def unsubscribe(self) -> None:
        self._feed_stop.set()
