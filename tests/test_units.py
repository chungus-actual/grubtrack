import pytest

from foodcore.units import fmt_amount, line_amount, parse_size, shelf


@pytest.mark.parametrize("text,cat,amount,dim", [
    ("1 lb 8 oz", "produce.vegetables", 680.389, "mass"),
    ("1.5 L", "dairy.milk", 1500, "volume"),
    ("32 oz", "pantry.grains", 907.185, "mass"),
    ("32 oz", "dairy.milk", 946.353, "volume"),       # liquid: fluid ounces
    ("dozen", "dairy.eggs", 12, "count"),
    ("2 dozen", "dairy.eggs", 24, "count"),
    ("3pk", None, 3, "count"),
    ("4 x 1.5 oz", "snacks.chips", 170.097, "mass"),
    ("12 oz (340 g)", "pantry.grains", 340.194, "mass"),  # restatement ignored
    ("1 quart", "dairy.plantmilk", 946.353, "volume"),
    ("1/2 lb", "deli.meats", 226.796, "mass"),
    ("52 fl oz", "dairy.milk", 1537.824, "volume"),
])
def test_parse_size(text, cat, amount, dim):
    got = parse_size(text, cat)
    assert got is not None and got[1] == dim
    assert got[0] == pytest.approx(amount, rel=1e-3)


def test_parse_size_nothing_measurable():
    assert parse_size("the usual", "dairy.yogurt") is None
    assert parse_size(None) is None


def test_kefir_ounces_are_fluid():
    assert parse_size("32 oz", "dairy.yogurt", "kefir")[1] == "volume"
    assert parse_size("32 oz", "dairy.yogurt", "yogurt")[1] == "mass"


def test_shelf_tag_bases():
    # by-weight produce on a receipt: 3.53 lb @ $2.29/lb
    amt, dim = line_amount(1, None, 3.53, "produce.fruit")
    v, label = shelf(8.08 / amt, "produce.fruit", dim)
    assert label == "lb" and v == pytest.approx(2.29, abs=0.01)
    # milk per fluid ounce
    amt, dim = line_amount(1, "1.5 L", None, "dairy.milk")
    v, label = shelf(6.49 / amt, "dairy.milk", dim)
    assert label == "fl oz" and v == pytest.approx(0.128, abs=0.001)
    # eggs per egg
    amt, dim = line_amount(1, "dozen", None, "dairy.eggs")
    assert shelf(6.99 / amt, "dairy.eggs", dim) == (pytest.approx(0.5825), "egg")
    # unknown size: per package
    assert line_amount(2, None, None, "snacks.chips") == (2.0, "pkg")


def test_fmt_amount():
    assert fmt_amount(1601.181, "mass") == "3.53 lb"
    assert fmt_amount(1500, "volume") == "1.5 L"
    assert fmt_amount(170.1, "mass") == "6 oz"
