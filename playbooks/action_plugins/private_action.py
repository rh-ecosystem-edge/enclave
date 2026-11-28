"""Delegate sensitive module execution without controller-side diagnostic traces."""

from ansible.plugins.action import ActionBase
from ansible.plugins.loader import action_loader, connection_loader

MODULES = {
    "ansible.builtin.command",
    "ansible.builtin.stat",
    "ansible.builtin.slurp",
    "ansible.builtin.file",
    "ansible.builtin.uri",
    "kubernetes.core.k8s_info",
}


class ActionModule(ActionBase):
    """Keep builtin action semantics while protecting module arguments and inputs."""

    _supports_async = True
    _supports_check_mode = True

    def run(
        self, tmp: str | None = None, task_vars: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Run the original action with its unchanged task and connection context."""
        module = self._task.args.get("module")
        if (
            not isinstance(module, str)
            or module not in MODULES
            or not isinstance(self._task.args.get("arguments"), dict)
        ):
            return {"failed": True, "msg": "Invalid private module request."}
        task = self._task.copy()
        task.action = module
        task.args = self._task.args["arguments"].copy()
        task.no_log = True
        privacy = connection_loader.get("private_ssh", class_only=True)
        with privacy.private_diagnostics():
            selected = action_loader.find_plugin_with_context(
                module, collection_list=task.collections
            )
            action = action_loader.get(
                module if selected.resolved else "ansible.builtin.normal",
                task=task,
                connection=self._connection,
                play_context=self._play_context,
                loader=self._loader,
                templar=self._templar,
                shared_loader_obj=self._shared_loader_obj,
                collection_list=task.collections,
            )
            result = action.run(tmp=tmp, task_vars=task_vars)
        result["_ansible_no_log"] = True
        return result
