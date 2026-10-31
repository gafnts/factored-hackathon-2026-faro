"""
POL-50 and POL-51 as code applies them to the model's reading of each message (ADR-0004, The graph, as amended on
2026-10-02). The call that reads a message says which language it is mostly in: es or pt sets the conversation's
language, unclear (words both languages share, digits, a bare "Ok") keeps it, and the conversation is in Spanish until a
message sets one. other gets POL-51's fixed reply whatever labels came with it, which the graph decides.
"""

LANGUAGES = ("es", "pt")
DEFAULT = "es"
OTHER = "other"


def settled(current: str, found: str | None) -> str:
    """
    The conversation's language after the model read a message as found.
    """
    return found if found in LANGUAGES else current
