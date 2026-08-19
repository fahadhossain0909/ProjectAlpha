"""Production-grade Auction Market Theory primitives.

The package is deliberately independent from the existing score-based auction
modules so callers can adopt structured AMT context incrementally.
"""

from .engine import AMTContext, AMTEngine, AuctionState, DayType, ValueMigration
from .volume_profile import VolumeProfile, build_volume_profile

__all__ = [
    "AMTContext",
    "AMTEngine",
    "AuctionState",
    "DayType",
    "ValueMigration",
    "VolumeProfile",
    "build_volume_profile",
]
