"""Vendor data errors used by the local Yahoo compatibility helpers."""


class VendorError(Exception):
    """Base for conditions where a vendor could not return usable data."""


class VendorUnavailableError(VendorError):
    """A vendor could not answer because a request was throttled or failed."""
