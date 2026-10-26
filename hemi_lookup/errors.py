class DataError(Exception):
    def __init__(self, code, message=None):
        if message is None:
            message, code = code, "invalid_data"
        self.code = code
        super().__init__(message)


class BudgetError(DataError):
    def __init__(self, message, *, limit=None):
        self.limit = limit
        super().__init__("budget_limit", message)


class DeadlineError(BudgetError):
    def __init__(self, message="Overall lookup deadline expired"):
        DataError.__init__(self, "time_budget_exhausted", message)


class CollectionStopped(Exception):
    """Operator cancellation, separate from source errors and retryable failures."""
