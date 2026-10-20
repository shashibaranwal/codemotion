"""User-facing errors: plain-English messages, never stack traces."""

from __future__ import annotations


class UserFacingError(Exception):
    """An error caused by user input, with a message fit to show in the UI or CLI.

    ``field`` names the request field at fault (e.g. ``"duration"``) when there is one.
    """

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, str | None]:
        return {"error": self.message, "field": self.field}
