import pytest

import banklib
from banklib.errors import UnknownProviderError


def test_list_providers():
    assert banklib.list_providers() == ["barclays", "natwest"]


def test_get_provider_returns_instance():
    p = banklib.get_provider("natwest")
    assert p.name == "natwest"
    assert p.display_name == "NatWest"
    # Same cached instance each time.
    assert banklib.get_provider("natwest") is p


def test_unknown_provider_raises():
    with pytest.raises(UnknownProviderError) as excinfo:
        banklib.get_provider("lloyds")
    # Error lists the known providers to help the caller.
    assert "barclays" in str(excinfo.value)
    assert "natwest" in str(excinfo.value)


def test_providers_declare_required_fields():
    nat = banklib.get_provider("natwest")
    assert [f.name for f in nat.credential_fields] == [
        "customer_number", "pin", "password"]
    bar = banklib.get_provider("barclays")
    assert [f.name for f in bar.credential_fields] == [
        "surname", "membership_number", "passcode", "memorable_word"]
