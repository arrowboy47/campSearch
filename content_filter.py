"""Content filtering for user submissions (reviews, etc).

Rejects slurs and hate speech on submission. Does not filter ordinary profanity,
which is valid in honest reviews ("this place was a shithole").

The blocklist is auditable and easy to edit: it lives here, not in config or
database. Word boundary matching prevents false positives (innocent substrings).
"""

import re

# Slurs and hate speech terms. Sorted alphabetically.
# Kept in one place so it is auditable and easy to edit.
_BLOCKED_TERMS = [
    # Slurs against various groups
    "chink",
    "fag",
    "faggot",
    "gook",
    "kike",
    "n1gg",
    "nigga",
    "nigger",
    "paki",
    "spic",
    "towelhead",
    "tranny",
    # Hate speech / derogatory terms
    "wetback",
]

# Compile regex with word boundaries so "Scunthorpe" (containing "cunt") is not blocked.
# Uses negative lookahead/lookbehind to ensure word boundaries: a letter, digit,
# underscore, or word boundary on both sides.
_BLOCKED_PATTERN = re.compile(
    r"(?<![a-z0-9_])(" + "|".join(re.escape(term) for term in _BLOCKED_TERMS) + r")(?![a-z0-9_])",
    re.IGNORECASE,
)


def has_blocked_content(text):
    """Check if text contains slurs or hate speech.

    Args:
        text: A string to check.

    Returns:
        True if blocked content is found, False otherwise.
    """
    if not text:
        return False
    return bool(_BLOCKED_PATTERN.search(text))
