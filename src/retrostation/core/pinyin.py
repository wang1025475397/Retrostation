"""Pinyin initials for the game search, without third-party dependencies.

Game titles are overwhelmingly GB2312 level-1 hanzi, whose pinyin letters run
in contiguous encoded ranges -- 23 ranges cover all ~3755 common characters.
The rare level-2 characters (radical-sorted, no letter runs) are skipped
rather than guessed: a title containing one simply matches on its remaining
letters.  ASCII letters and digits pass through upper-cased, so both
``initials("龙与地下城2 汉化版")`` and ``initials("Street Fighter 2")`` yield
something the letter grid can type.
"""

from __future__ import annotations

#: ``(first GBK code, last GBK code) -> pinyin letter`` for GB2312 level-1
#: characters.  Boundaries follow the classic per-letter runs of the
#: pinyin-sorted level-1 table; I, U and V never start a pinyin syllable, so
#: they have no ranges.
_RANGES: tuple[tuple[tuple[int, int], str], ...] = (
    ((0xB0A1, 0xB0C4), "A"),
    ((0xB0C5, 0xB2C0), "B"),
    ((0xB2C1, 0xB4ED), "C"),
    ((0xB4EE, 0xB6E9), "D"),
    ((0xB6EA, 0xB7A1), "E"),
    ((0xB7A2, 0xB8C0), "F"),
    ((0xB8C1, 0xB9FD), "G"),
    ((0xB9FE, 0xBBF6), "H"),
    ((0xBBF7, 0xBFA5), "J"),
    ((0xBFA6, 0xC0AB), "K"),
    ((0xC0AC, 0xC2E7), "L"),
    ((0xC2E8, 0xC4C2), "M"),
    ((0xC4C3, 0xC5B5), "N"),
    ((0xC5B6, 0xC5BD), "O"),
    ((0xC5BE, 0xC6D9), "P"),
    ((0xC6DA, 0xC8BA), "Q"),
    ((0xC8BB, 0xC8F5), "R"),
    ((0xC8F6, 0xCBF9), "S"),
    ((0xCBFA, 0xCDD9), "T"),
    ((0xCDDA, 0xCEF3), "W"),
    ((0xCEF4, 0xD1B8), "X"),
    ((0xD1B9, 0xD4D0), "Y"),
    ((0xD4D1, 0xD7F9), "Z"),
)

#: Full-width forms in GBK row 0xA3: digits 0-9, upper-case and lower-case
#: letters.  Titles picked from Chinese sites are full of these.
_FULLWIDTH_DIGITS = (0xA3B0, 0xA3B9)
_FULLWIDTH_UPPER = (0xA3C1, 0xA3DA)
_FULLWIDTH_LOWER = (0xA3E1, 0xA3FA)


def initials(text: str) -> str:
    """Search initials: pinyin first letters for hanzi, word initials for
    Latin words, every digit kept.

    "Street Fighter 2" and "StreetFighter2" both give ``SF2`` -- matching how
    a player types.  Hanzi each contribute their pinyin letter (``超级马里奥``
    -> ``CJMLA``: 奥 is "ao", so A).  Characters outside the covered ranges
    (level-2 hanzi, kana, ...) are skipped: they would only add noise.
    """
    out: list[str] = []
    prev_word = False    # previous character belonged to a Latin word
    prev_lower = False   # ... and was lower-case (camel-case detection)
    for ch in text:
        if ch.isascii():
            folded = ch
        else:
            code = ch.encode("gbk", errors="ignore")
            if len(code) != 2:
                prev_word = prev_lower = False
                continue
            value = (code[0] << 8) | code[1]
            if _FULLWIDTH_DIGITS[0] <= value <= _FULLWIDTH_DIGITS[1]:
                folded = chr(value - _FULLWIDTH_DIGITS[0] + ord("0"))
            elif _FULLWIDTH_UPPER[0] <= value <= _FULLWIDTH_UPPER[1]:
                folded = chr(value - _FULLWIDTH_UPPER[0] + ord("A"))
            elif _FULLWIDTH_LOWER[0] <= value <= _FULLWIDTH_LOWER[1]:
                folded = chr(value - _FULLWIDTH_LOWER[0] + ord("a"))
            else:
                prev_word = prev_lower = False
                for (lo, hi), letter in _RANGES:
                    if lo <= value <= hi:
                        out.append(letter)
                        break
                continue
        if folded.isdigit():
            out.append(folded)
            prev_word, prev_lower = True, False
        elif folded.isalpha():
            if not prev_word or (prev_lower and folded.isupper()):
                out.append(folded.upper())
            prev_word, prev_lower = True, folded.islower()
        else:
            prev_word = prev_lower = False
    return "".join(out)
