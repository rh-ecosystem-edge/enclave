"""Resolve only the matrix task's explicitly configured SSH common arguments."""

from ansible.plugins.action import ActionBase
from ansible.plugins.loader import connection_loader


class ActionModule(ActionBase):
    """Use builtin SSH configuration precedence without opening a connection."""

    def run(
        self, tmp: str | None = None, task_vars: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Keep resolved options private and leave absent options empty."""
        result = super().run(tmp, task_vars)
        privacy = connection_loader.get("private_ssh", class_only=True)
        with privacy.private_diagnostics():
            builtin = connection_loader.get(
                "ansible.builtin.ssh", self._play_context, None
            )
            variables = {}
            if task_vars and "ansible_ssh_common_args" in task_vars:
                variables["ansible_ssh_common_args"] = self._templar.template(
                    task_vars["ansible_ssh_common_args"]
                )
            builtin.set_options(var_options=variables)
            arguments = builtin.get_option("ssh_common_args")
        result.update({
            "ansible_facts": {"_network_ssh_args": arguments},
            "_ansible_no_log": True,
            "changed": False,
        })
        return result
