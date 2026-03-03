# Configuration Variables

This directory contains the configuration files needed for your enclave deployment.

## Quick Start

1. **Copy the example files to create your configuration:**
   ```bash
   cd config/
   cp global.example.yaml global.yaml
   cp certificates.example.yaml certificates.yaml
   ```

2. **Fill in `global.yaml` with your environment details**
3. **Fill in `certificates.yaml` with your SSL certificates**

## Configuration Files

### `global.yaml` (required)
Contains all cluster and infrastructure configuration.

### `certificates.yaml` (required)
Contains SSL certificates for the cluster.

## Security
- **Never commit `global.yaml` or `certificates.yaml` to version control**
- These files contain sensitive credentials and private keys
- The `.gitignore` is configured to exclude them

## Getting Help
- See `docs/DEPLOYMENT_GUIDE.md` for step-by-step deployment instructions
- See `docs/CONFIGURATION_REFERENCE.md` for detailed parameter documentation
