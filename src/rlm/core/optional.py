"""Explicit optional capability checks without importing provider integrations."""

from importlib.util import find_spec


def require_inference() -> None:
    """Require inference only when a provider-backed operation is requested."""
    try:
        available = find_spec("litellm") is not None
    except ModuleNotFoundError as error:
        if error.name != "litellm":
            raise
        available = False
    if not available:
        raise ImportError(
            "Inference support is not installed. Install the optional inference extra: "
            "pip install 'snipara-sandbox[inference]'. "
            "Deterministic validation and MCP do not require inference."
        )
