class UumiError(Exception):
    pass


class RevisionConflictError(UumiError):
    pass


class LeaseConflictError(UumiError):
    pass


class TransitionRejectedError(UumiError):
    pass


class RunNotFoundError(UumiError):
    pass


class ActiveRunConflictError(UumiError):
    pass


class IdempotencyConflictError(UumiError):
    pass


class StorageIntegrityError(UumiError):
    pass


class OutboxLeaseError(UumiError):
    pass


class AuthenticationError(UumiError):
    pass


class AuthorizationError(UumiError):
    pass


class ResourceNotFoundError(UumiError):
    pass


class ResourceConflictError(UumiError):
    pass


class ApprovalError(UumiError):
    pass


class PlaybookError(UumiError):
    pass


class CapabilityError(UumiError):
    pass
