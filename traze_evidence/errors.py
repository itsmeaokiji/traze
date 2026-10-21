"""Stable machine-readable verification failures."""


class VerificationError(ValueError):
    def __init__(self, code, message, *, incomplete=False):
        super().__init__(message)
        self.code = code
        self.status = "incomplete" if incomplete else "invalid"


def require(condition, code, message, *, incomplete=False):
    if not condition:
        raise VerificationError(code, message, incomplete=incomplete)
