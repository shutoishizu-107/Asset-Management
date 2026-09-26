"""Reusable configured CSV/JSON adapter for official structured downloads."""
from .base import ConfiguredStructuredProvider


class OfficialStructuredProvider(ConfiguredStructuredProvider):
    """Provider id is supplied by a tiny provider-specific subclass."""