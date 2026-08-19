from datetime import datetime, timedelta, timezone

from aitos.intelligence.amt.tpo_profile import TPOObservation, build_tpo_profile


def test_tpo_profile_uses_time_brackets_not_volume():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observations = []
    for bracket in range(4):
        ts = start + timedelta(minutes=30 * bracket)
        observations.extend([TPOObservation(ts, 100.0), TPOObservation(ts, 101.0)])
    observations.append(TPOObservation(start + timedelta(minutes=90), 102.0))

    profile = build_tpo_profile(observations, tick_size=1.0, bracket_minutes=30)

    assert profile.bracket_count == 4
    assert profile.poc == 100.0
    assert 101.0 in profile.single_prints or 102.0 in profile.single_prints
    assert profile.vah >= profile.poc >= profile.val


def test_poor_extreme_and_excess_candidates_are_explicit():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observations = []
    for bracket in range(3):
        ts = start + timedelta(minutes=30 * bracket)
        observations.extend([TPOObservation(ts, 100.0), TPOObservation(ts, 101.0)])
    observations.extend([
        TPOObservation(start + timedelta(minutes=90), 102.0),
        TPOObservation(start + timedelta(minutes=120), 102.0),
    ])

    profile = build_tpo_profile(observations, tick_size=1.0, bracket_minutes=30)
    assert profile.poor_high
    assert not profile.poor_low
