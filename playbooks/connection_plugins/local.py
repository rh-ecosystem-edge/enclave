"""Keep local connection facts out of controller DEBUG diagnostics."""

import sys

from ansible.plugins.loader import connection_loader

BuiltinConnection = connection_loader.get("ansible.builtin.local", class_only=True)
DOCUMENTATION = sys.modules[BuiltinConnection.__module__].DOCUMENTATION


class Connection(BuiltinConnection):
    """Preserve the builtin local transport, protecting only its fact traces."""

    def update_vars(self, variables: dict[str, object]) -> None:
        """Populate unchanged connection facts without logging configured SSH keys."""
        privacy = connection_loader.get("private_ssh", class_only=True)
        with privacy.private_diagnostics():
            super().update_vars(variables)
