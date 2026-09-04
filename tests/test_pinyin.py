"""Pinyin initials: the letter runs must map the GB2312 level-1 table right."""

import pytest

from retrostation.core.pinyin import initials

# The classic first-character-of-each-letter-run mnemonic for the pinyin-sorted
# GB2312 level-1 table ("啊芭擦搭蛾发噶哈击喀…").  If any of these is wrong, a
# range boundary in the table is off.
_MNEMONIC = [
    ("啊", "A"), ("芭", "B"), ("擦", "C"), ("搭", "D"), ("蛾", "E"),
    ("发", "F"), ("噶", "G"), ("哈", "H"), ("击", "J"), ("喀", "K"),
    ("垃", "L"), ("妈", "M"), ("拿", "N"), ("哦", "O"), ("啪", "P"),
    ("期", "Q"), ("然", "R"), ("撒", "S"), ("塌", "T"), ("挖", "W"),
    ("昔", "X"), ("压", "Y"), ("匝", "Z"),
]


@pytest.mark.parametrize(("char", "letter"), _MNEMONIC)
def test_level1_run_starts(char: str, letter: str) -> None:
    assert initials(char) == letter


def test_common_titles() -> None:
    assert initials("中文测试") == "ZWCS"
    assert initials("龙与地下城") == "LYDXC"
    assert initials("魂斗罗") == "HDL"
    assert initials("超级马里奥") == "CJMLA"
    assert initials("拳皇") == "QH"
    assert initials("街机") == "JJ"
    assert initials("游戏") == "YX"


def test_mixed_and_ascii() -> None:
    assert initials("龙与地下城2 汉化版") == "LYDXC2HHB"
    # Latin words contribute their initial (camel case splits too), digits stay.
    assert initials("Street Fighter 2") == "SF2"
    assert initials("StreetFighter2") == "SF2"
    assert initials("Mario 64") == "M64"
    assert initials("") == ""
    assert initials("  -  ") == ""


def test_fullwidth_forms_fold_to_ascii() -> None:
    assert initials("２") == "2"
    # A full-width letter run folds into one word, so it contributes one
    # initial -- same rule as "StreetFighter2".  Name-containment matching
    # (which folds via NFKC) covers typed-in substrings like "AB".
    assert initials("ＡＢｃ") == "A"


def test_unmappable_characters_are_skipped() -> None:
    # Kana have no pinyin letter; skip them rather than guess.
    assert initials("ドラクエ") == ""
    # Punctuation never enters the initials string.
    assert initials("龙·与·地") == "LYD"
