"""Pure parsers that turn a bank's raw statement data into
:class:`~banklib.models.Transaction` objects.

Kept free of any browser/Selenium code so they can be unit-tested directly
against captured fixtures.
"""

from .barclays_pdf import ParsedStatement, extract_text, parse_barclays_statement
from .natwest_csv import parse_natwest_csv

__all__ = ["ParsedStatement", "extract_text", "parse_barclays_statement",
           "parse_natwest_csv"]
