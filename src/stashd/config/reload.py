"""Re-reading the cluster config the controller authored.

A reload is rejected whole, and the running config is kept, whenever the new file does not
validate or its revision does not identify it: the revision and the content hash are what
every daemon compares against, so two different configs may not share a revision.
"""

from pathlib import Path

from stashd.config.cluster import ConfigDocument, load_cluster_document
from stashd.config.errors import ConfigError


def reload_document(path: Path, running: ConfigDocument) -> ConfigDocument | None:
    """The new config, or None when the file says the same thing as the running one."""
    candidate = load_cluster_document(path)
    if candidate.content_hash == running.content_hash:
        return None
    if candidate.revision == running.revision:
        raise ConfigError(
            path,
            [
                f"the config changed but is still revision {running.revision}: "
                "raise the revision so daemons can tell the two apart"
            ],
        )
    if candidate.revision < running.revision:
        raise ConfigError(
            path,
            [
                f"revision {candidate.revision} is below the running {running.revision}: "
                "revisions are monotonic"
            ],
        )
    return candidate
