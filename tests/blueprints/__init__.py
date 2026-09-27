"""One case per blueprint, in one file per group so authors do not collide.

A new group file needs a line below. test_every_blueprint_has_a_case fails for a
blueprint whose case is missing, and for a case left behind by a removed blueprint.
"""

from .climate import CASES as CLIMATE
from .generic import CASES as GENERIC
from .home import CASES as HOME
from .kit import Case
from .people import CASES as PEOPLE

CASES: dict[str, Case] = {**GENERIC, **HOME, **CLIMATE, **PEOPLE}

__all__ = ["CASES", "Case"]
