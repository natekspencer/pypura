"""Exceptions."""


class PuraApiException(Exception):
    """General Pura API exception."""

    def __init__(self, *args: object, status_code: int | None = None) -> None:
        """Initialize, with the HTTP status code if the API returned an error."""
        super().__init__(*args)
        self.status_code = status_code


class PuraAuthenticationError(PuraApiException):
    """To indicate there is an issue authenticating."""
