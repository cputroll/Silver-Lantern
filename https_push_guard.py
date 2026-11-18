"""Disabled experimental HTTPS push guard; this does not authorize publication.

Do not enable until transport identity, lease and probe semantics, cross-host
serialization, private marker ACLs, and crash-durable marker creation/removal
(including directory durability) are independently established. Evidence must
not contain a destination URL or other credentials. Existing evidence requires
manual review and must never be cleared merely because a process disappeared.
"""


def guarded_push(transport, candidate, *, expected, marker):
    """Fail closed without accessing transport, filesystem, or arguments."""
    raise RuntimeError('HTTPS push guard disabled: safety guarantees unverified')
