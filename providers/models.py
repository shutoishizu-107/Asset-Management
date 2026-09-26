"""Normalized external data records and publication gates."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@dataclass(frozen=True)
class DataRecord:
    schema_version: int
    provider_id: str
    provider_name: str
    metric: str
    subject: str
    status: str
    value: Any
    unit: str | None
    source_name: str
    source_url: str | None
    fetched_at: str
    as_of: str | None
    expires_at: str | None
    report_period: str | None
    stale: bool
    freshness_status: str
    confidence: str
    license_status: str
    cache_allowed: bool | str
    redistribution_allowed: bool | str
    raw_data_publication_allowed: bool | str
    derived_data_publication_allowed: bool | str
    data_class: str
    data_origin: str
    source_role: str
    reason: str | None = None
    raw_sha256: str | None = None
    age_days: int | None = None

    def __post_init__(self):
        if self.status not in {'available', 'unavailable'}:
            raise ValueError('status must be available or unavailable')
        if self.status == 'available' and (self.value is None or not self.as_of):
            raise ValueError('available records require value and as_of')
        if self.status == 'unavailable' and self.value is not None:
            raise ValueError('unavailable records cannot carry a value')
        if self.freshness_status not in {'fresh', 'stale', 'unavailable'}:
            raise ValueError('freshness_status must be fresh, stale, or unavailable')
        if self.status == 'unavailable' and self.freshness_status != 'unavailable':
            raise ValueError('unavailable records must use unavailable freshness')
        if self.data_class not in {'raw', 'derived', 'status'}:
            raise ValueError('data_class must be raw, derived, or status')
        if self.data_origin not in {'real', 'sample', 'unknown'}:
            raise ValueError('data_origin must be real, sample, or unknown')
        if self.source_role not in {'primary', 'fallback', 'development'}:
            raise ValueError('source_role must be primary, fallback, or development')

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def unavailable_record(provider: dict[str, Any], metric: str, subject: str, reason: str, source_url: str | None = None) -> DataRecord:
    return DataRecord(
        schema_version=1,
        provider_id=provider['provider_id'],
        provider_name=provider['provider_name'],
        metric=metric,
        subject=subject,
        status='unavailable',
        value=None,
        unit=None,
        source_name=provider['provider_name'],
        source_url=source_url or provider.get('base_url'),
        fetched_at=utc_now(),
        as_of=None,
        expires_at=None,
        report_period=None,
        stale=True,
        freshness_status='unavailable',
        confidence='unavailable',
        license_status=provider.get('license_status', 'unreviewed'),
        cache_allowed=provider.get('cache_allowed', False),
        redistribution_allowed=provider.get('redistribution_allowed', 'verify'),
        raw_data_publication_allowed=provider.get('raw_data_publication_allowed', False),
        derived_data_publication_allowed=provider.get('derived_data_publication_allowed', False),
        data_class='status',
        data_origin='unknown',
        source_role='primary',
        reason=reason,
    )


def publication_allowed(record: DataRecord) -> bool:
    if record.data_origin == 'sample':
        return False
    if record.status != 'available':
        return True
    # Records are embedded in public analysis JSON as well as rendered pages.
    if record.cache_allowed is not True:
        return False
    if record.data_class == 'raw':
        return record.raw_data_publication_allowed is True
    if record.data_class == 'derived':
        return record.derived_data_publication_allowed is True
    return True


def public_projection(records: list[DataRecord]) -> list[dict[str, Any]]:
    """Keep availability/status visible, but block unlicensed values from web output."""
    result = []
    for record in records:
        if publication_allowed(record):
            result.append(record.to_dict())
        else:
            masked = record.to_dict()
            masked.update(status='unavailable', value=None, reason='publication_not_permitted', confidence='unavailable', stale=True, freshness_status='unavailable')
            result.append(masked)
    return result