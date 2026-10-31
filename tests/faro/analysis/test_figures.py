"""
The selection's figures label only what fails or sits on the threshold.
"""

import polars as pl

from faro.analysis.figures import (
    MODEL,
    SCORE,
    field_populations,
    learned_signals,
)
from faro.analysis.selection import Selection


def test_labels_only_fields_that_fail_or_sit_on_the_line(selection: Selection) -> None:
    data = field_populations(selection).data
    assert isinstance(data, pl.DataFrame)
    labels = dict(zip(data["field"].cast(pl.String), data["label"], strict=True))

    assert labels["account_inquiries|transactions.merchant_name"] == "no rows"
    assert labels["card_support|products.expiration_date"] == ""
    assert labels["disputes|transactions.fraud_score"] == "80.0%"


def test_draws_each_auc_beside_fraud_score(selection: Selection) -> None:
    data = learned_signals(selection).data
    assert isinstance(data, pl.DataFrame)
    drawn = {
        (str(c), str(s)): round(a, 3)
        for c, s, a in data.select("candidate", "series", "auc").iter_rows()
    }

    assert drawn == {
        ("Card support", MODEL): 0.71,
        ("Card support", SCORE): 0.84,
        ("Transaction-dispute intake", MODEL): 0.505,
    }
