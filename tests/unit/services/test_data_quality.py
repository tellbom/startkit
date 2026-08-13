from stock_strategy_api.services.data_quality import maximum_missing_symbols, missing_symbols_within_gate


def test_five_percent_missing_symbol_gate_for_csi300():
    assert maximum_missing_symbols(300) == 15
    assert missing_symbols_within_gate(15, 300)
    assert not missing_symbols_within_gate(16, 300)


def test_gate_does_not_round_up_fractional_symbol():
    assert maximum_missing_symbols(21) == 1
    assert missing_symbols_within_gate(1, 21)
    assert not missing_symbols_within_gate(2, 21)
