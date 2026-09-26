""""Did you mean" suggestions for unknown names (DESIGN.md §9).

Runs only on the error path, so a plain O(len(a) * len(b)) Levenshtein is plenty.
Suggestions are advisory: the compiler still fails, it never auto-corrects.
"""
from collections.abc import Iterable

DEFAULT_THRESHOLD = 0.4
DEFAULT_LIMIT = 3


def levenshtein(a: str, b: str) -> int:
    """Minimum number of single-character inserts, deletes and substitutions turning a into b."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1,                 # delete
                               current[j - 1] + 1,              # insert
                               previous[j - 1] + (ca != cb)))   # substitute
        previous = current
    return previous[-1]


def score(a: str, b: str) -> float:
    """Case-insensitive edit distance normalised by the longer string: 0.0 identical, 1.0 unrelated."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return levenshtein(a.lower(), b.lower()) / longest


def suggest(key: str, candidates: Iterable[str],
            threshold: float = DEFAULT_THRESHOLD, limit: int = DEFAULT_LIMIT) -> tuple[str, ...]:
    """Candidates scoring <= threshold against key, best first, ties broken by name."""
    scored = sorted((score(key, name), name) for name in set(candidates))
    return tuple(name for s, name in scored if s <= threshold)[:limit]
