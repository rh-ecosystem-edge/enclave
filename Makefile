# Enclave Lab - Makefile
# Runs directly on the Landing Zone (no scripts/ dependency)

.PHONY: help setup setup-env setup-ansible validate-config validate-schema \
        deploy-cluster deploy-cluster-prepare deploy-cluster-mirror \
        deploy-cluster-pre-install-validate \
        deploy-cluster-install deploy-cluster-post-install deploy-cluster-operators \
        deploy-cluster-day2 deploy-cluster-discovery deploy-cluster-connected \
        deploy-plugin mirror-plugin bootstrap sync

# Configuration
WORKING_DIR ?= $(HOME)
DISCONNECTED ?= true
PLUGIN ?=
GLOBAL_VARS ?= config/global.yaml
CERTS_VARS ?= config/certificates.yaml
CLOUD_INFRA_VARS ?= config/cloud_infra.yaml

# Ansible
AP = ansible-playbook
AP_FLAGS = -e workingDir=$(WORKING_DIR) -e disconnected=$(DISCONNECTED)

# Default target
help:
	@echo "Enclave Lab - Landing Zone Makefile"
	@echo ""
	@echo "Setup targets:"
	@echo "  make setup                            - Run full setup (setup-env + setup-ansible)"
	@echo "  make setup-env                        - Install system packages and dependencies"
	@echo "  make setup-ansible                    - Install Ansible collections and roles"
	@echo ""
	@echo "Validation targets:"
	@echo "  make validate-config                  - Validate configuration files"
	@echo "  make validate-schema                  - Validate configuration against JSON schemas"
	@echo ""
	@echo "Deployment targets:"
	@echo "  make deploy-cluster                   - Deploy OpenShift cluster (all phases)"
	@echo "  make deploy-cluster-prepare           - Phase 1: Download binaries and content"
	@echo "  make deploy-cluster-mirror            - Phase 2: Mirror registry setup (disconnected)"
	@echo "  make deploy-cluster-pre-install-validate - Validate servers are ready before install"
	@echo "  make deploy-cluster-install           - Phase 3: Deploy OpenShift cluster"
	@echo "  make deploy-cluster-post-install      - Phase 4: Cluster configuration"
	@echo "  make deploy-cluster-operators         - Phase 5: Install operators"
	@echo "  make deploy-cluster-day2              - Phase 6: Day-2 operations"
	@echo "  make deploy-cluster-discovery         - Phase 7: Configure hardware discovery"
	@echo "  make deploy-cluster-connected         - Deploy in connected mode (DISCONNECTED=false)"
	@echo ""
	@echo "Plugin deployment:"
	@echo "  make deploy-plugin PLUGIN=<name>      - Deploy a single plugin (e.g., openshift-ai)"
	@echo "  make mirror-plugin PLUGIN=<name>      - Mirror a single plugin (e.g., openshift-ai)"
	@echo ""
	@echo "Convenience targets:"
	@echo "  make bootstrap                        - Bootstrap the Landing Zone"
	@echo "  make sync                             - Sync configuration to the Landing Zone"
	@echo ""
	@echo "Configuration variables:"
	@echo "  WORKING_DIR      - Working directory (default: $$HOME)"
	@echo "  DISCONNECTED     - Disconnected mode (default: true)"
	@echo "  PLUGIN           - Plugin name for deploy-plugin target"
	@echo "  GLOBAL_VARS      - Path to global vars file (default: config/global.yaml)"
	@echo "  CERTS_VARS       - Path to certificates vars file (default: config/certificates.yaml)"
	@echo "  CLOUD_INFRA_VARS - Path to cloud infra vars file (default: config/cloud_infra.yaml)"
	@echo ""
	@echo "Current values:"
	@echo "  WORKING_DIR=$(WORKING_DIR)"
	@echo "  DISCONNECTED=$(DISCONNECTED)"
	@echo ""
	@echo "Examples:"
	@echo "  make deploy-cluster                                     # Full disconnected deployment"
	@echo "  make deploy-cluster-connected                           # Full connected deployment"
	@echo "  WORKING_DIR=/home/cloud-user make deploy-cluster        # Custom working directory"
	@echo "  make deploy-plugin PLUGIN=openshift-ai                  # Deploy a plugin"

# Setup targets
setup: setup-env setup-ansible

setup-env:
	@sudo bash ./setup_env.sh

setup-ansible:
	@bash ./setup_ansible.sh

# Validation targets
validate-config:
	@bash ./validations.sh --global-vars $(GLOBAL_VARS) --certs-vars $(CERTS_VARS)

validate-schema:
	@$(AP) playbooks/validate-schema.yaml -e@$(GLOBAL_VARS) -e@$(CERTS_VARS) --tags schema-validation

# Deploy targets
deploy-cluster:
	@$(AP) playbooks/main.yaml $(AP_FLAGS)

deploy-cluster-prepare:
	@$(AP) playbooks/01-prepare.yaml $(AP_FLAGS)

deploy-cluster-mirror:
	@$(AP) playbooks/02-mirror.yaml $(AP_FLAGS)

deploy-cluster-pre-install-validate:
	@$(AP) playbooks/03-deploy.yaml $(AP_FLAGS) --tags pre-install-validate

deploy-cluster-install:
	@$(AP) playbooks/03-deploy.yaml $(AP_FLAGS)

deploy-cluster-post-install:
	@$(AP) playbooks/04-post-install.yaml $(AP_FLAGS)

deploy-cluster-operators:
	@$(AP) playbooks/05-operators.yaml $(AP_FLAGS)

deploy-cluster-day2:
	@$(AP) playbooks/06-day2.yaml $(AP_FLAGS)

deploy-cluster-discovery:
	@$(AP) playbooks/07-configure-discovery.yaml $(AP_FLAGS) -e@$(CLOUD_INFRA_VARS)

deploy-cluster-connected:
	@$(MAKE) deploy-cluster DISCONNECTED=false

# Plugin deployment
deploy-plugin:
	@if [ -z "$(PLUGIN)" ]; then \
		echo "Error: PLUGIN variable must be set. Usage: make deploy-plugin PLUGIN=<name>"; \
		exit 1; \
	fi
	@$(AP) playbooks/deploy-plugin.yaml $(AP_FLAGS) -e 'plugin_name=$(PLUGIN)'

mirror-plugin:
	@if [ -z "$(PLUGIN)" ]; then \
		echo "Error: PLUGIN variable must be set. Usage: make mirror-plugin PLUGIN=<name>"; \
		exit 1; \
	fi
	@$(AP) playbooks/deploy-plugin.yaml $(AP_FLAGS) -e 'plugin_name=$(PLUGIN)' --tags mirror

# Convenience targets
bootstrap:
	@bash ./bootstrap.sh --global-vars $(GLOBAL_VARS) --certs-vars $(CERTS_VARS)

sync:
	@bash ./sync.sh $(GLOBAL_VARS) $(CERTS_VARS)
