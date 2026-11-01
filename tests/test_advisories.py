"""
An advisory we accept says why, and until when, at most 90 days ahead, so accepting one is a decision we retake (ADR-0010; OPS-11).
"""

import datetime
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_each_accepted_advisory_has_a_reason_and_an_end_within_90_days() -> None:
    accepted = tomllib.loads((ROOT / "osv-scanner.toml").read_text())["IgnoredVulns"]
    latest = datetime.date.today() + datetime.timedelta(days=90)
    for entry in accepted:
        assert entry["reason"].strip(), entry["id"]
        until = entry["ignoreUntil"]
        assert isinstance(until, datetime.date), entry["id"]
        until = until.date() if isinstance(until, datetime.datetime) else until
        assert until <= latest, entry["id"]
