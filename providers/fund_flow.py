"""Fund-flow provider adapter for licensed structured data only."""
from __future__ import annotations

from typing import Any

from .base import ConfiguredStructuredProvider, ProviderError


class FundFlowProvider(ConfiguredStructuredProvider):
    provider_id = 'fund_flow'

    def fetch(self, provider: dict[str, Any], resource: dict[str, Any], api_key: str | None = None):
        if provider.get('license_status') != 'approved' or provider.get('acquisition_status') != 'approved':
            raise ProviderError('fund_flow_license_unverified')
        return super().fetch(provider, resource, api_key)