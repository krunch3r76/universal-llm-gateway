# Pipeline Definitions

This directory is for static/bundled pipeline definitions.

## Structure

```
pipelines/
├── {domain}/
│   ├── *.yaml           # Pipeline specs
│   ├── prompts.yaml     # Domain prompts (namespace = directory name)
│   ├── models.yaml      # Domain model refs
│   └── handlers/        # Domain handlers (optional)
│       ├── __init__.py  # Must have register_handlers(router)
│       └── *.py
```

## Usage

Production pipelines are in `~/.local/share/universal-stargate/pipelines/`

To bundle a pipeline into the repo, copy its entire domain directory here.
The loader treats repo and local identically - later search paths override earlier.
