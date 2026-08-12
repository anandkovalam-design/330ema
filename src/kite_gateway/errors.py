class GatewayError(RuntimeError):
    """Base gateway error safe to show to an operator."""


class ConfigurationError(GatewayError):
    """Configuration is missing or unsafe."""


class AuthenticationRequired(GatewayError):
    """A valid Kite session is required."""


class LiveTradingLocked(GatewayError):
    """Live order mutations are blocked by the safety gate."""


class BrokerOperationError(GatewayError):
    """The broker rejected or failed an operation."""
