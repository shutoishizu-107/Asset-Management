"""Index provider adapter for explicitly configured structured downloads."""
from .official_structured import OfficialStructuredProvider


class IndexProvider(OfficialStructuredProvider):
    provider_id = 'index_provider'


class MsciProvider(OfficialStructuredProvider):
    provider_id = 'msci'


class FtseRussellProvider(OfficialStructuredProvider):
    provider_id = 'ftse_russell'


class SpGlobalProvider(OfficialStructuredProvider):
    provider_id = 'sp_global'


class NasdaqIndexesProvider(OfficialStructuredProvider):
    provider_id = 'nasdaq_indexes'