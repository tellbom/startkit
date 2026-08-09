from __future__ import annotations


class DomainError(RuntimeError):
    code = "domain_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(DomainError):
    code = "configuration_error"


class DataUnavailableError(DomainError):
    code = "data_unavailable"


class InvalidDateError(DomainError):
    code = "invalid_trade_date"


class UnknownStrategyError(DomainError):
    code = "unknown_strategy"


class RunConflictError(DomainError):
    code = "run_conflict"


class ResourceNotFoundError(DomainError):
    code = "resource_not_found"
