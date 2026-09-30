"""Which exposed entities a command is most likely about, when they do not all fit.

The conversation agent sends at most MAX_CONVERSATION_ENTITIES entities. A house
with more than that used to lose the ones last in entity_id order, whatever the
command said, so "turn on the zebra lamp" could never reach light.zebra_lamp.
This ranks them by the words the command shares with each entity's names, its
area and its floor, with BM25, the scoring search engines use for short queries.
It runs in Home Assistant and sends nothing, so it costs no tokens.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

# The usual BM25 constants. k1 limits how much a word said twice counts, and b
# how much a long list of names is marked down against a short one.
K1 = 1.2
B = 0.75

# Scripts written without spaces between words. A run of them is one \w+ match,
# so "打开台灯" would be a single word that no name ever equals. Each character
# counts as a word instead.
_UNSPACED = "぀-ヿ㐀-䶿一-鿿가-힯"
_WORD = re.compile(rf"[{_UNSPACED}]|[^\W{_UNSPACED}]+")


def words(text: str) -> list[str]:
    """The words of a text, case folded, in the order they appear."""
    return _WORD.findall(text.casefold())


def bm25(query: Iterable[str], documents: Sequence[Sequence[str]]) -> list[float]:
    """One score for each document: how well it matches the query words.

    A word that many documents share, such as "light" in a house of lights, adds
    little. A word only one document has, such as "zebra", adds a lot.
    """
    count = len(documents)
    if not count:
        return []
    average = sum(len(d) for d in documents) / count or 1.0
    frequency = Counter(word for document in documents for word in set(document))
    wanted = set(query)
    scores: list[float] = []
    for document in documents:
        found = Counter(word for word in document if word in wanted)
        score = 0.0
        for word, times in found.items():
            weight = math.log(
                (count - frequency[word] + 0.5) / (frequency[word] + 0.5) + 1
            )
            norm = K1 * (1 - B + B * len(document) / average)
            score += weight * times * (K1 + 1) / (times + norm)
        scores.append(score)
    return scores
