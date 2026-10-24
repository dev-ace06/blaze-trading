from algo.broker.kotak_neo import KotakNeoBroker
from algo.broker.paper import PaperBroker


class FakeNeo:
    def __init__(self):
        self.calls = []

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        return {"nOrdNo": "42"}

    def cancel_order(self, **kwargs):
        return {"status": "success", **kwargs}

    def positions(self):
        return {"data": []}

    def limits(self):
        return {"data": {"Net": 100000}}


def test_kotak_adapter_uses_current_sdk_canonical_order_parameters():
    client = FakeNeo()
    broker = KotakNeoBroker(client=client)
    broker.place_order("RELIANCE-EQ", "BUY", 5)
    assert client.calls == [
        {
            "exchange_segment": "nse_cm",
            "product": "MIS",
            "price": "0",
            "order_type": "MKT",
            "quantity": "5",
            "validity": "DAY",
            "trading_symbol": "RELIANCE-EQ",
            "transaction_type": "B",
            "trigger_price": "0",
        }
    ]


def test_paper_broker_needs_no_credentials_and_fills_from_supplied_quote():
    broker = PaperBroker(initial_cash=100_000, quote_provider=lambda symbol: 100.0, slippage=0.0003)
    order = broker.place_order("INFY", "BUY", 10)
    assert order.status == "FILLED"
    assert order.average_price == 100.03
    assert broker.positions()["INFY"] == 10

