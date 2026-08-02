"""Exception classes for universal_concurrency."""


class CapacityLimitError(ValueError):
    """Raised when capacity limit is invalid (≤0)."""

    pass


class OverReleaseError(RuntimeError):
    """Raised when release() called more times than acquire()."""

    pass


class TransferHolderError(RuntimeError):
    """Raised when transfer_holder() is called with a non-holder from_id."""

    pass


class CrossLaneTransferError(TransferHolderError):
    """Raised when park/restore would install a holder on the wrong lane gate."""

    pass
