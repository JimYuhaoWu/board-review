"""Scoring profiles: Martian's category sets, plus the categories other datasets add.

Martian's file (martian/score_profiles.py) is vendored unchanged. Qodo's repository-rule
violations (category `rule`, eval/runner/qodo.py) are counted only by the All profile.
"""

from __future__ import annotations

from .martian.score_profiles import PROFILE_CATEGORIES as _MARTIAN

EXTRA_ALL = frozenset({"rule"})

PROFILE_CATEGORIES: dict[str, frozenset[str]] = {
    **_MARTIAN,
    "all": _MARTIAN["all"] | EXTRA_ALL,
}
