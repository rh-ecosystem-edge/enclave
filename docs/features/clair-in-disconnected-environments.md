# Clair in disconnected environments

This feature enables image scanning using Clair in a disconnected environment by exporting vulnerability data in the Landing Zone and importing it to the database.

## Hotfix implementation

1. If quay-operator is installed in version 3.16:
    1. Update `defaults/operators.yaml` quay-operator channel to `stable-3.15`.
    1. Execute the mirror phase:
        ```sh
        $ ansible-playbook -e @config/global.yaml playbooks/02-mirror.yaml
        ```
    1. Uninstall the existing instance:
        ```sh
        $ oc -n quay-enterprise delete quayregistry registry
        $ oc -n quay-enterprise delete secret quay-config
        $ oc -n quay-enterprise delete subscription quay-operator
        $ oc -n quay-enterprise delete csv quay-operator.v3.16.1
        ```

1. Add this section to the `quay-config` Secret (`operators/quay-operator/tasks.yaml`):
    ```yaml
        clair-config.yaml: |
          indexer:
            airgap: true
            scanner:
              repo:
                rhel-repository-scanner:
                  repo2cpe_mapping_file: "/data/repository-to-cpe.json"
              package:
                rhel_containerscanner:
                  name2repos_mapping_file: "/data/container-name-repos-map.json"
          matcher:
            disable_updaters: true
    ```

1. Add the `clair_disconnected.yaml` tasks file to `operators/quay-operator/`.

1. Include the tasks in `operators/quay-operator/tasks.yaml`:
    ```yaml
    - name: Clair in disconnected environments
      ansible.builtin.include_tasks:
        file: clair_disconnected.yaml
    ```

1. Execute the post-install phase (or just run the operators task directly):
    ```sh
    $ ansible-playbook -e @config/global.yaml playbooks/04-post-install.yaml
    # Or run operators task directly:
    $ ansible-playbook -e @config/global.yaml playbooks/tasks/configure_operators.yaml
    ```
