"""Strategier skrevet af research-agenterne.

Tilføj nye klasser her og registrér dem med ``register_strategy``. Ændr ALDRIG en
klasse, der er registreret som kandidat i lab/registry.json - lav en ny version.
"""

from __future__ import annotations

from . import indicators as ind  # noqa: F401
from .strategies import Strategy, _frame, register_strategy  # noqa: F401
