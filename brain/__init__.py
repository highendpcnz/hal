"""Provider-neutral local intelligence boundary for HAL."""

from .base import BrainProvider, BrainProviderError, KeyedLocks

__all__ = ["BrainProvider", "BrainProviderError", "KeyedLocks"]
