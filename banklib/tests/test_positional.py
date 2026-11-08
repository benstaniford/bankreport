import pytest

from banklib.browser import leading_number, ordinal_number
from banklib.errors import LoginError
from banklib.tests.conftest import FakeElement


# -- position parsers -------------------------------------------------------

def test_leading_number():
    assert leading_number("3rd number") == 3
    assert leading_number("  2") == 2
    assert leading_number("no digits") is None


def test_ordinal_number():
    assert ordinal_number("2nd character") == 2
    assert ordinal_number("Enter the 11th character") == 11
    assert ordinal_number("character") is None


# -- fill_positional loop ---------------------------------------------------

def _driver_with_inputs(make_browser, input_css, specs):
    """specs: list of (id, label_text, displayed). Returns a Browser."""
    elements = {input_css: [FakeElement(el_id=i, displayed=d) for i, _, d in specs]}
    for el_id, label, _ in specs:
        elements[f"label[for='{el_id}']"] = [FakeElement(text=label)]
    return make_browser(elements=elements)


def test_fills_requested_positions(make_browser):
    css = "input[id^='pin-']"
    browser = _driver_with_inputs(make_browser, css, [
        ("pin-a", "1st number", True),
        ("pin-b", "3rd number", True),
    ])
    n = browser.fill_positional(css, leading_number, "1234", "PIN")
    assert n == 2
    inputs = browser.driver.elements[css]
    assert inputs[0].value == "1"   # position 1 of "1234"
    assert inputs[1].value == "3"   # position 3 of "1234"


def test_skips_hidden_when_visible_only(make_browser):
    css = "input[id^='mem-']"
    browser = _driver_with_inputs(make_browser, css, [
        ("mem-a", "2nd character", False),   # hidden duplicate
        ("mem-b", "2nd character", True),
    ])
    n = browser.fill_positional(css, ordinal_number, "secret", "memorable",
                                visible_only=True)
    assert n == 1
    assert browser.driver.elements[css][0].value == ""     # hidden untouched
    assert browser.driver.elements[css][1].value == "e"    # "secret"[1]


def test_position_beyond_secret_raises(make_browser):
    css = "input[id^='pin-']"
    browser = _driver_with_inputs(make_browser, css, [
        ("pin-a", "9th number", True),
    ])
    with pytest.raises(LoginError, match="shorter"):
        browser.fill_positional(css, leading_number, "1234", "PIN")


def test_unparsable_label_raises(make_browser):
    css = "input[id^='pin-']"
    browser = _driver_with_inputs(make_browser, css, [
        ("pin-a", "mystery", True),
    ])
    with pytest.raises(LoginError, match="Can't parse"):
        browser.fill_positional(css, leading_number, "1234", "PIN")
