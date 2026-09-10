"""self-orch — agent-agnostic parallel substrate rail."""

__version__ = "0.1.0"

from .rail import dispatch, SeatSpec, DispatchResult

__all__ = ["dispatch", "SeatSpec", "DispatchResult", "__version__"]
