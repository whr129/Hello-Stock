from decimal import Decimal

import pytest

from news_agent.research.analysis import _trust_score as analysis_trust_score
from news_agent.research.link_validation import _trust_score as link_trust_score
from news_agent.research.scoring import _float


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.8", 0.8),
        (0.9, 0.9),
        (1, 1.0),
        (Decimal("0.7"), 0.7),
        (memoryview(b"0.6"), 0.6),
        (None, 0.0),
        ([], 0.0),
        ({"score": 0.9}, 0.0),
        ("bad", 0.0),
    ],
)
def test_evidence_numbers_preserve_numeric_values_and_default_invalid_values(value, expected):
    assert analysis_trust_score({"trust_score": value}) == expected
    assert link_trust_score({"trust_score": value}) == expected
    assert _float(value) == expected
