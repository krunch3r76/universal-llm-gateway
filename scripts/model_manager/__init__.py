"""
Model Manager CLI - Unified tool for model catalog management.

Commands:
    discover    Scan directory for uncataloged models
    generate    Generate catalog entries from model files
    verify      Verify model origin against HuggingFace
    download    Download model from verified registry
    list        List models in catalog (--local, --static, --merged)
    info/show   Show detailed model information
    export      Export model from static to local catalog
    remove      Remove model from local catalog
    init        Initialize local catalog directory
    validate    Validate catalog schema
    update      Update model metadata in local catalog
    measure     Measure VRAM/RAM profiles via Gateway Job API
    remeasure   Re-measure profiles for existing models
"""

# Pytest can import this file as a top-level module when collection walks a
# symlink to scripts/model_manager (no package parent). The relative import
# is only valid inside the package.
if __package__:
    from .cli import main
else:
    main = None

__all__ = ["main"]
