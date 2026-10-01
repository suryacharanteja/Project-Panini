"""Actionable failures that are safe to show and speak to the user."""


class BrowserUnavailable(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
