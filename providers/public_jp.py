"""Structured-download adapters for official Japanese public NISA sources."""
from .official_structured import OfficialStructuredProvider


class FsaProvider(OfficialStructuredProvider):
    provider_id = 'fsa'


class ToushinProvider(OfficialStructuredProvider):
    provider_id = 'toushin'