import pytest

from algo.portfolio import CostModel, Portfolio


def test_paper_fill_applies_adverse_slippage_both_sides():
    portfolio = Portfolio(100_000, CostModel(slippage=0.0003))
    buy = portfolio.open_position("INFY", "LONG", 10, 100, 99, "breakout")
    assert buy.entry_price == pytest.approx(100.03)
    trade = portfolio.close_position("INFY", 101, "target")
    assert trade.exit_price == pytest.approx(100.9697)
    assert trade.net_pnl < trade.gross_pnl


def test_nse_cost_model_charges_sell_stt_buy_stamp_and_gst():
    costs = CostModel().round_trip_charges(100, 101, 100)
    expected = 2.525 + 0.59697 + 0.0201 + 0.3 + (0.59697 + 0.0201) * 0.18
    assert costs == pytest.approx(expected, rel=1e-6)


def test_partial_exit_leaves_remainder_and_realizes_costed_pnl():
    portfolio = Portfolio(100_000, CostModel(slippage=0))
    portfolio.open_position("TCS", "LONG", 10, 100, 99, "entry")
    trade = portfolio.close_position("TCS", 102, "two_r", qty=5)
    assert portfolio.positions["TCS"].qty == 5
    assert trade.qty == 5
    assert portfolio.realized_pnl == trade.net_pnl

