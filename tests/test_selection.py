from algo.selection import Candidate, rank_candidates


def test_selection_filters_price_liquidity_and_opening_range_then_ranks():
    rows = [
        Candidate("A", 200, 300, 2.0, 1.0, 2.0, 0.5, 0.0),
        Candidate("B", 210, 300, 2.0, 3.0, 4.0, 0.7, 0.5),
        Candidate("CHEAP", 99, 1000, 2.0, 9.0, 9.0, 1.0, 1.0),
        Candidate("ILLIQ", 500, 100, 2.0, 9.0, 9.0, 1.0, 1.0),
        Candidate("DEAD", 500, 300, 2.0, 1.0, 2.0, 0.1, 0.0),
        Candidate("WILD", 500, 300, 2.0, 1.0, 2.0, 6.0, 0.0),
    ]
    ranked = rank_candidates(rows, top_n=5)
    assert [row.symbol for row in ranked] == ["B", "A"]
    assert ranked[0].score > ranked[1].score


def test_held_symbols_stay_when_watchlist_is_reranked():
    rows = [Candidate(str(i), 200, 300, 2, i, i, 0.5, 0) for i in range(1, 8)]
    ranked = rank_candidates(rows, top_n=3, held_symbols={"1"})
    assert "1" in {row.symbol for row in ranked}
    assert len(ranked) == 3

