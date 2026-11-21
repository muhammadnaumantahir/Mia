"""Cheap, deterministic language-family hint for routing (MIA-GW-03).

Mia's owner converses in English, Roman Urdu and Urdu script. Small models are
usually much weaker in the last two, so the router uses this hint to avoid
*rewarding* tiny models for those turns. It is a heuristic over the text only:

* ``urdu_script``: a meaningful share of letters is Arabic-script (this cannot tell
  Urdu from Arabic/Persian; for routing that distinction does not matter).
* ``roman_urdu``: several distinctive Roman Urdu function words, in a high enough
  share of the words. Ambiguous English words are deliberately not in the list.
* ``en``: everything else, including text we are unsure about (fail neutral).

It never excludes a model; it only reorders eligible ones, and it makes no claim
about any specific model's measured Urdu quality.
"""

from __future__ import annotations

import re

_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_ROMAN_URDU = frozenset(
    "hai hain nahi nahin kya mujhe mujhko aap apna apni apne tum tumhara tumhein kaise kaisa kaisi "
    "kyun kyu kyon bata batao bataiye theek thik acha accha shukriya mera meri mere hum yeh woh aur "
    "lekin karna karo kijiye chahiye chahta chahti raha rahi rahe tha thi abhi bohat bahut kuch koi "
    "sab mein liye wala wali samajh madad sakta sakti haal kaam dobara pehle baad".split()
)
_WORD = re.compile(r"[a-z']+")


def detect_language(text: str) -> str:
    sample = (text or "")[:2000]
    letters = [ch for ch in sample if ch.isalpha()]
    if not letters:
        return "en"
    arabic = sum(1 for ch in letters if any(lo <= ord(ch) <= hi for lo, hi in _ARABIC_RANGES))
    if arabic / len(letters) >= 0.3:
        return "urdu_script"
    words = _WORD.findall(sample.lower())
    hits = sum(1 for w in words if w in _ROMAN_URDU)
    if hits >= 2 and hits / max(1, len(words)) >= 0.25:
        return "roman_urdu"
    return "en"
