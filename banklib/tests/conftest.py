"""Shared test doubles.

The real browser is never started in the suite: providers are handed a
:class:`banklib.browser.Browser` wrapping a :class:`FakeDriver`, and the pure
parsing cores are tested directly. Live bank login cannot run in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow running the suite from a checkout without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from banklib.browser import Browser  # noqa: E402


class FakeElement:
    """Stands in for a Selenium WebElement."""

    def __init__(self, el_id: str | None = None, text: str = "",
                 displayed: bool = True):
        self._id = el_id
        self.text = text
        self._displayed = displayed
        self.typed: list[str] = []
        self.cleared = False
        self.clicked = False

    def get_attribute(self, name: str):
        return self._id if name == "id" else None

    def is_displayed(self) -> bool:
        return self._displayed

    def clear(self) -> None:
        self.cleared = True

    def send_keys(self, value: str) -> None:
        self.typed.append(value)

    def click(self) -> None:
        self.clicked = True

    @property
    def value(self) -> str:
        return "".join(self.typed)


class FakeDriver:
    """Minimal WebDriver double keyed by selector string and script substring.

    ``elements`` maps a selector (CSS or id) to a list of :class:`FakeElement`.
    ``scripts`` is a list of ``(substring, value_or_callable)`` pairs; the first
    whose substring appears in the executed script wins.
    """

    def __init__(self, elements: dict | None = None,
                 scripts: list | None = None, current_url: str = ""):
        self.elements = elements or {}
        self.scripts = scripts or []
        self.current_url = current_url
        self.quit_called = False
        self.window_size = None

    def find_elements(self, by, selector):
        return list(self.elements.get(selector, []))

    def find_element(self, by, selector):
        els = self.elements.get(selector, [])
        if not els:
            from selenium.common.exceptions import NoSuchElementException
            raise NoSuchElementException(selector)
        return els[0]

    def execute_script(self, script, *args):
        for key, value in self.scripts:
            if key in script:
                return value(*args) if callable(value) else value
        return None

    def get(self, url):
        self.current_url = url

    def quit(self):
        self.quit_called = True

    def save_screenshot(self, path):
        return True

    def set_window_size(self, w, h):
        self.window_size = (w, h)


@pytest.fixture
def make_browser():
    """Factory: build a Browser over a FakeDriver with the given elements/scripts."""

    def _make(elements=None, scripts=None, current_url=""):
        return Browser(FakeDriver(elements=elements, scripts=scripts,
                                  current_url=current_url))

    return _make
