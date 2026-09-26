"""Japanese mutual fund adapter for configured official CSV/JSON disclosures."""
from .official_structured import OfficialStructuredProvider


class JapaneseMutualFundProvider(OfficialStructuredProvider):
    provider_id = 'mutual_fund_jp'