"""Exceptions."""


class PuraApiException(Exception):
    """General Pura API exception."""


class PuraAuthenticationError(PuraApiException):
    """To indicate there is an issue authenticating."""
