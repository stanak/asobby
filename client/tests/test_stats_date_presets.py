from datetime import date
from unittest.mock import patch

from stats_date_presets import date_preset_range


@patch("stats_date_presets.jst_today", return_value=date(2026, 7, 30))
def test_date_preset_ranges(mock_today):
    del mock_today
    assert date_preset_range("today") == ("2026-07-30", "2026-07-30")
    assert date_preset_range("yesterday") == ("2026-07-29", "2026-07-29")
    assert date_preset_range("this_month") == ("2026-07-01", "2026-07-30")
    assert date_preset_range("this_year") == ("2026-01-01", "2026-07-30")
