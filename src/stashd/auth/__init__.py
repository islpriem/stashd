"""Authentication providers. They prove who asked; they never impersonate."""

from stashd.auth.provider import AuthProvider, Unauthenticated

__all__ = ["AuthProvider", "Unauthenticated"]
