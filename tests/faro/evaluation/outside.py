"""
A small outside source for the tests, written by us in the shape the real one takes (ADR-0011, Wording sources), which
stays out of the repository until the first play of a set drawn from it.
"""

from pathlib import Path

from faro.evaluation import families

ROOT = Path(__file__).parent / "sources"
FAMILIES, ANSWERS = families.load_source(families.OUTSIDE, ROOT)
