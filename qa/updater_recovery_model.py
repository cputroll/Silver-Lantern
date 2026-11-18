"""Offline decision sketch only: inputs are assumptions, not verified evidence.

Run IDs and attestations are not validated; authentication, serialization and job
emptiness are assumed inputs. Never use this to remove a real recovery marker or
authorize publication.
"""
from enum import Enum


class Decision(Enum):
    HOLD = 'hold'
    RECONCILE = 'reconcile'


def decide(marker_run, attested_run, authenticated, verified_empty, serialized):
    """A positive receipt permits reconciliation, not publication or marker removal."""
    if (isinstance(marker_run, str) and marker_run and marker_run == attested_run
            and authenticated is True and verified_empty is True and serialized is True):
        return Decision.RECONCILE
    return Decision.HOLD
