"""Running work as the requesting user.

MUNGE proves who asked; acting as them is a separate, host-local privilege mechanism.
How that is done is still open; the interim decision is `sudo -n -u <user>`, behind this
interface, with a root-and-drop implementation as the configurable alternative.
"""

from stashd.identity.base import Completed, Identity, IdentityError

__all__ = ["Completed", "Identity", "IdentityError"]
