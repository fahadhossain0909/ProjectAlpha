from aitos.intelligence.amt.decision_fusion import fuse_amt_context
from aitos.intelligence.amt.engine import AMTContext, AuctionState, DayType, ValueMigration
from aitos.intelligence.amt.volume_profile import VolumeProfile


def _context(**kwargs):
    profile = VolumeProfile(bins=((100.0, 10.0), (101.0, 20.0), (102.0, 10.0)), poc=101.0, vah=102.0, val=100.0, total_volume=40.0)
    base = dict(profile=profile, state=AuctionState.ACCEPTANCE, day_type=DayType.NORMAL,
                value_migration=ValueMigration.UP, acceptance=0.8, rejection=0.1,
                price_location=0.8, ib_high=102.0, ib_low=100.0, confidence=0.9,
                rationale=(), book_imbalance=0.3)
    base.update(kwargs)
    return AMTContext(**base)


def test_aligned_long_scores_above_neutral():
    signal = fuse_amt_context(_context(), "long")
    assert signal.score > 5
    assert signal.veto is False
    assert signal.confidence > 0


def test_strong_opposite_rejection_vetoes_amt_signal():
    signal = fuse_amt_context(_context(state=AuctionState.REJECTION, acceptance=0.1, rejection=0.9,
                                       value_migration=ValueMigration.DOWN, book_imbalance=-0.5), "long")
    assert signal.veto is True
    assert signal.score < 5
