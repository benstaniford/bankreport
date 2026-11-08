from banklib.money import format_money, parse_money


def test_parse_plain():
    assert parse_money("£1,234.56") == 1234.56


def test_parse_negative():
    assert parse_money("-£1,234.56") == -1234.56


def test_parse_embedded_in_text():
    assert parse_money("Available: £42.00 remaining") == 42.00


def test_parse_no_money():
    assert parse_money("Select Account") is None
    assert parse_money("") is None
    assert parse_money(None) is None


def test_format_positive():
    assert format_money(1234.5) == "£1,234.50"


def test_format_negative():
    assert format_money(-99.9) == "-£99.90"


def test_format_none_is_blank():
    assert format_money(None) == ""


def test_round_trip():
    for s in ("£0.00", "-£1,000,000.00", "£7.07"):
        assert format_money(parse_money(s)) == s
