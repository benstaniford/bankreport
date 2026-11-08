"""Importing this subpackage registers every bundled provider.

Add a new bank by dropping a module here that defines a ``@register``-decorated
:class:`banklib.base.Provider` subclass and importing it below.
"""

from . import barclays, natwest  # noqa: F401
