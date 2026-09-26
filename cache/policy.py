"""External data cache TTL policy; all TTL values come from cache_policy.yaml."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class CachePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CachePolicy:
    metric_ttl_days: dict[str, int]
    zero_is_missing: bool

    @classmethod
    def load(cls, path: Path) -> 'CachePolicy':
        try:
            payload = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError) as error:
            raise CachePolicyError(f'Cannot load cache policy ({type(error).__name__})') from None
        if payload.get('schema_version') != 1 or not isinstance(payload.get('metric_ttl_days'), dict):
            raise CachePolicyError('Unsupported cache policy schema')
        ttl = payload['metric_ttl_days']
        if not ttl or any(not isinstance(metric, str) or type(days) is not int or days < 0 for metric, days in ttl.items()):
            raise CachePolicyError('Every metric TTL must be a nonnegative integer day count')
        return cls(metric_ttl_days=dict(ttl), zero_is_missing=bool(payload.get('zero_is_missing', True)))

    def ttl_days(self, metric: str) -> int:
        try:
            return self.metric_ttl_days[metric]
        except KeyError:
            raise CachePolicyError(f'TTL is unavailable for metric: {metric}') from None