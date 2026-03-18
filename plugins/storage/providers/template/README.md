# Custom Storage Provider Template

To add a custom storage provider:

1. Copy this directory to a new provider name:
   ```
   cp -r plugins/storage/providers/template plugins/storage/providers/my-storage
   ```

2. Edit `deploy.yaml` with your storage deployment tasks.

3. Edit `validate.yaml` with your storage validation tasks.

4. Run the storage plugin with your provider:
   ```
   make deploy-plugin PLUGIN=storage -e storage_provider=my-storage
   ```
