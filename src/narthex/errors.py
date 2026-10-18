"""Narthex core exceptions."""


class NarthexError(Exception):
    """Base exception for all Narthex failures."""


class TopologyError(NarthexError):
    """Raised when a topology definition is invalid."""


class AgentResolutionError(NarthexError):
    """Raised when a topology references an agent that is not registered."""


class CapabilityError(NarthexError):
    """Raised when an agent tries to use a capability it does not hold."""


class ToolDenied(CapabilityError):
    """Raised when a tool call is blocked by the capability boundary."""


class ToolNotFound(ToolDenied):
    """Raised when an unknown tool is requested."""


class ApprovalDenied(NarthexError):
    """Raised when a Cathedral approval gate rejects an action."""


class ApprovalEscalated(NarthexError):
    """Raised when an approval gate cannot decide and escalates."""


class SnapshotError(NarthexError):
    """Raised when a snapshot cannot be read, written, or replayed."""
