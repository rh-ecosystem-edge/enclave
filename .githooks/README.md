# Project Git Hooks

This directory contains custom Git hooks to maintain commit standards and track AI tool usage.

## Setup

Git does not automatically use hooks stored in the repository. To enable these hooks locally, run the following command in the project root:

```bash
git config core.hooksPath .githooks
```

## Included Hooks

### prepare-commit-msg

Purpose: Ensures AI-assisted work is properly attributed using the "Assisted-by: {tool}" trailer.

Behavior:
- Scans the commit message for an existing "Assisted-by: " pattern.
- If missing, it prompts the user in the terminal: "Did an AI help author this commit?"
- If yes, it asks for the tool name and appends it to the bottom of the commit message.
- If no, it proceeds without changes.

## Troubleshooting

### Permissions

If the hooks are not firing after configuration, you may need to restore execution permissions:

```bash
chmod +x .githooks/prepare-commit-msg
```

## Bypassing

In rare emergencies where a hook is preventing a critical commit, you can bypass it using the --no-verify flag:

```bash
git commit -m "urgent fix" --no-verify
```

(Note: Use this sparingly as it skips AI attribution checks.)
