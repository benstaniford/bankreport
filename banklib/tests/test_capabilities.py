import pytest

import banklib
from banklib import Capability
from banklib.errors import UnsupportedCapability


def test_natwest_supports_download():
    p = banklib.get_provider("natwest")
    assert p.supports(Capability.BALANCES)
    assert p.supports(Capability.DOWNLOAD)


def test_barclays_has_no_download():
    p = banklib.get_provider("barclays")
    assert p.supports(Capability.BALANCES)
    assert not p.supports(Capability.DOWNLOAD)


def test_barclays_download_raises():
    p = banklib.get_provider("barclays")
    with pytest.raises(UnsupportedCapability):
        p.download(browser=None, accounts=None, download_dir=".")


def test_session_download_gated(monkeypatch, tmp_path):
    """Session.download refuses on a provider without the capability, before
    touching the browser."""
    p = banklib.get_provider("barclays")
    session = banklib.Session(p, {"surname": "x", "membership_number": "y",
                                  "passcode": "12345", "memorable_word": "z"},
                              download_dir=tmp_path)
    with pytest.raises(UnsupportedCapability):
        session.download()
