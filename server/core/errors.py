class FireKeyError(Exception):
    pass


class RevisionConflictError(FireKeyError):
    pass


class LeaseConflictError(FireKeyError):
    pass


class TransitionRejectedError(FireKeyError):
    pass


class RunNotFoundError(FireKeyError):
    pass


class ActiveRunConflictError(FireKeyError):
    pass


class IdempotencyConflictError(FireKeyError):
    pass


class StorageIntegrityError(FireKeyError):
    pass


class OutboxLeaseError(FireKeyError):
    pass
