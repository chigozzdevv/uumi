class FireKeyError(Exception):
    pass


class RevisionConflictError(FireKeyError):
    pass


class LeaseConflictError(FireKeyError):
    pass


class TransitionRejectedError(FireKeyError):
    pass
