from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .base import HttpsRedirectHandler, Observation, ProviderError, validate_safe_source_url


class VanguardProvider:
    provider_id = 'vanguard'

    def fetch(self, provider: dict, resource: dict, api_key: str | None = None):
        subject = str(resource.get('subject') or resource.get('entity_id') or 'VT')
        metric = str(resource.get('metric') or '')
        url = str(resource.get('url') or f'https://investor.vanguard.com/irr/funds/profile/{subject}')
        payload, raw, http_status = self._request_json(url)

        dashboard = payload.get('dashboard') if isinstance(payload, dict) else None
        if not isinstance(dashboard, dict):
            raise ProviderError('invalid_json')

        if metric == 'expense_ratio':
            value = self._fraction_from_percent(dashboard.get('expenseRatio'))
            as_of = self._iso_date(dashboard.get('expenseRatioAsOfDate'))
            if value is None or as_of is None:
                raise ProviderError('provider_returned_no_observations')
            observations = [Observation(
                subject=subject,
                metric='expense_ratio',
                value=value,
                unit='fraction',
                as_of=as_of,
                source_url=url,
                data_class='raw',
                data_origin='real',
            )]
            return observations, raw, {'http_status': http_status}

        if metric in {'aum', 'inception_date'}:
            value = self._find_payload_value(payload, {
                'aum': ('aum', 'assetsUnderManagement', 'totalNetAssets', 'netAssets', 'fundAssets'),
                'inception_date': ('inceptionDate', 'fundInceptionDate', 'startDate'),
            }[metric])
            if metric == 'aum':
                value = self._number(value)
                unit = 'USD'
            else:
                value = self._iso_date(value)
                unit = 'date'
            as_of = self._iso_date(
                dashboard.get('asOfDate')
                or dashboard.get('asOf')
                or payload.get('asOfDate')
                or payload.get('asOf')
            )
            if value is None or as_of is None:
                raise ProviderError('provider_returned_no_observations')
            return [Observation(
                subject=subject,
                metric=metric,
                value=value,
                unit=unit,
                as_of=as_of,
                source_url=url,
                data_class='raw',
                data_origin='real',
            )], raw, {'http_status': http_status}

        if metric == 'price_history':
            observations = self._price_history_observations(payload, subject, url)
            if not observations:
                raise ProviderError('provider_returned_no_observations')
            return observations, raw, {'http_status': http_status}

        if metric == 'benchmark_price_history':
            observations = self._benchmark_history_observations(payload, subject, url)
            if not observations:
                raise ProviderError('provider_returned_no_observations')
            return observations, raw, {'http_status': http_status}

        if metric == 'holdings':
            observations = self._individual_holdings_observations(payload, subject, url)
            if not observations:
                raise ProviderError('holdings_not_available_from_source')
            return observations, raw, {'http_status': http_status}

        if metric == 'region_weights':
            observation = self._region_weights_observation(payload, subject, url)
            if observation is None:
                raise ProviderError('provider_returned_no_observations')
            return [observation], raw, {'http_status': http_status}

        if metric == 'sector_weights':
            observation = self._sector_weights_observation(payload, subject, url)
            if observation is None:
                raise ProviderError('provider_returned_no_observations')
            return [observation], raw, {'http_status': http_status}

        if metric == 'benchmark':
            observation = self._benchmark_name_observation(payload, subject, url)
            if observation is None:
                raise ProviderError('provider_returned_no_observations')
            return [observation], raw, {'http_status': http_status}

        if metric == 'risk_free_rate':
            observation = self._risk_free_rate_observation(payload, subject, url)
            if observation is None:
                raise ProviderError('provider_returned_no_observations')
            return [observation], raw, {'http_status': http_status}

        raise ProviderError('provider_metric_not_supported')

    def _price_history_observations(self, payload: dict[str, Any], subject: str, source_url: str) -> list[Observation]:
        annual_items = self._annual_return_items(payload)
        points = self._series_from_return_rows(annual_items, 'totalRtn')
        as_of = self._iso_date(payload.get('performancefees', {}).get('totalReturns', {}).get('annualReturn', {}).get('asOfDate'))
        if not points or as_of is None:
            return []
        return [Observation(
            subject=subject,
            metric='price_history',
            value=points,
            unit='index',
            as_of=as_of,
            source_url=source_url,
            data_class='raw',
            data_origin='real',
            report_period='annual',
        )]

    def _benchmark_history_observations(self, payload: dict[str, Any], subject: str, source_url: str) -> list[Observation]:
        annual_items = self._annual_return_items(payload)
        points = self._series_from_return_rows(annual_items, 'benchmarkTotalRtn')
        as_of = self._iso_date(payload.get('performancefees', {}).get('totalReturns', {}).get('annualReturn', {}).get('asOfDate'))
        if not points or as_of is None:
            return []
        return [Observation(
            subject=subject,
            metric='benchmark_price_history',
            value=points,
            unit='index',
            as_of=as_of,
            source_url=source_url,
            data_class='raw',
            data_origin='real',
            report_period='annual',
        )]

    def _individual_holdings_observations(self, payload: dict[str, Any], subject: str, source_url: str) -> list[Observation]:
        portfolio = payload.get('portfolioComposition', {})
        weighted = portfolio.get('weightedExposures', {}) if isinstance(portfolio, dict) else {}
        # Candidate structured paths for individual security holdings.
        candidate_lists = [
            portfolio.get('holdings') if isinstance(portfolio, dict) else None,
            portfolio.get('topHoldings') if isinstance(portfolio, dict) else None,
            payload.get('holdings') if isinstance(payload, dict) else None,
            payload.get('topHoldings') if isinstance(payload, dict) else None,
        ]
        rows: list[tuple[str, str | None, str | None, str | None, float, str]] = []
        as_of = None
        for candidate in candidate_lists:
            if not isinstance(candidate, list):
                continue
            for item in candidate:
                if not isinstance(item, dict):
                    continue
                security_name = str(
                    item.get('securityName')
                    or item.get('name')
                    or item.get('holdingName')
                    or ''
                ).strip()
                if not security_name or self._looks_aggregate_label(security_name):
                    continue
                weight = self._fraction_from_percent(item.get('weight') if item.get('weight') is not None else item.get('value'))
                if weight is None:
                    continue
                try:
                    weight_value = float(weight)
                except ValueError:
                    continue
                ticker = str(item.get('ticker') or '').strip() or None
                isin = str(item.get('isin') or '').strip() or None
                cusip = str(item.get('cusip') or '').strip() or None
                asset_class = str(item.get('assetClass') or item.get('asset_class') or 'equity').strip() or 'equity'
                item_as_of = self._iso_date(item.get('asOfDate') or item.get('as_of'))
                as_of = as_of or item_as_of
                rows.append((security_name, ticker, isin, cusip, weight_value, asset_class))
        if not rows:
            return []
        if as_of is None:
            region = weighted.get('region', {}) if isinstance(weighted, dict) else {}
            as_of = self._iso_date(region.get('asOfDate')) if isinstance(region, dict) else None
        if as_of is None:
            return []
        total = sum(weight for _, _, _, _, weight, _ in rows)
        if total <= 0:
            return []
        complete = abs(total - 1.0) <= 0.03
        normalized = [(name, ticker, isin, cusip, weight / total, asset_class) for name, ticker, isin, cusip, weight, asset_class in rows]
        return [
            Observation(
                subject=subject,
                metric='holdings',
                value={
                    'security_name': name,
                    'ticker': ticker,
                    'isin': isin,
                    'cusip': cusip,
                    'weight': format(weight, 'f'),
                    'asset_class': asset_class,
                    'complete': complete,
                },
                unit='fraction',
                as_of=as_of,
                source_url=source_url,
                data_class='raw',
                data_origin='real',
                report_period='monthly',
            )
            for name, ticker, isin, cusip, weight, asset_class in normalized
        ]

    @staticmethod
    def _looks_aggregate_label(name: str) -> bool:
        text = name.strip().lower()
        if text.startswith(('region::', 'sector::', 'asset::')):
            return True
        return bool(re.search(r'\b(region|sector|allocation|market|exposure|style)\b', text))

    def _region_weights_observation(self, payload: dict[str, Any], subject: str, source_url: str) -> Observation | None:
        weighted = payload.get('portfolioComposition', {}).get('weightedExposures', {})
        region = weighted.get('region', {}) if isinstance(weighted, dict) else {}
        exposures = region.get('exposure', []) if isinstance(region, dict) else []
        as_of = self._iso_date(region.get('asOfDate')) if isinstance(region, dict) else None
        if not isinstance(exposures, list) or as_of is None:
            return None
        value: dict[str, str] = {}
        total = Decimal('0')
        for item in exposures:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            fraction = self._fraction_from_percent(item.get('value'))
            if not name or fraction is None:
                continue
            value[name] = fraction
            total += Decimal(fraction)
        if not value or total <= 0:
            return None
        return Observation(
            subject=subject,
            metric='region_weights',
            value=value,
            unit='fraction',
            as_of=as_of,
            source_url=source_url,
            data_class='raw',
            data_origin='real',
            report_period='monthly',
        )

    def _sector_weights_observation(self, payload: dict[str, Any], subject: str, source_url: str) -> Observation | None:
        weighted = payload.get('portfolioComposition', {}).get('weightedExposures', {})
        sector = weighted.get('sectorExposure', {}) if isinstance(weighted, dict) else {}
        if not isinstance(sector, dict) or sector.get('isavailable') is not True:
            return None
        exposures = sector.get('exposure', [])
        as_of = self._iso_date(sector.get('asOfDate'))
        if not isinstance(exposures, list) or as_of is None:
            return None
        value: dict[str, str] = {}
        total = Decimal('0')
        for item in exposures:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            fraction = self._fraction_from_percent(item.get('value'))
            if not name or fraction is None:
                continue
            value[name] = fraction
            total += Decimal(fraction)
        if not value or total <= 0:
            return None
        return Observation(
            subject=subject,
            metric='sector_weights',
            value=value,
            unit='fraction',
            as_of=as_of,
            source_url=source_url,
            data_class='raw',
            data_origin='real',
            report_period='monthly',
        )

    def _benchmark_name_observation(self, payload: dict[str, Any], subject: str, source_url: str) -> Observation | None:
        annual = payload.get('performancefees', {}).get('totalReturns', {}).get('annualReturn', {})
        if not isinstance(annual, dict):
            return None
        name = str(annual.get('benchmarkShortName') or '').strip()
        as_of = self._iso_date(annual.get('asOfDate'))
        if not name or as_of is None:
            return None
        return Observation(
            subject=subject,
            metric='benchmark',
            value=name,
            unit='name',
            as_of=as_of,
            source_url=source_url,
            data_class='raw',
            data_origin='real',
            report_period='annual',
        )

    def _risk_free_rate_observation(self, payload: dict[str, Any], subject: str, source_url: str) -> Observation | None:
        # VT profile payload does not currently expose a risk-free rate series/value.
        _ = payload
        _ = subject
        _ = source_url
        return None

    @staticmethod
    def _annual_return_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        annual = payload.get('performancefees', {}).get('totalReturns', {}).get('annualReturn', {})
        annual_returns = annual.get('annualReturns', []) if isinstance(annual, dict) else []
        if not isinstance(annual_returns, list) or not annual_returns:
            return []
        first = annual_returns[0]
        items = first.get('item') if isinstance(first, dict) else None
        if not isinstance(items, list):
            return []
        return [row for row in items if isinstance(row, dict) and str(row.get('year', '')).isdigit()]

    def _series_from_return_rows(self, rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        ordered = sorted(rows, key=lambda row: int(str(row.get('year'))))
        level = Decimal('100')
        points: list[dict[str, Any]] = []
        for row in ordered:
            value = self._fraction_from_percent(row.get(key))
            if value is None:
                continue
            growth = Decimal('1') + Decimal(value)
            if growth <= 0:
                continue
            level *= growth
            points.append({'date': f"{int(row['year'])}-12-31", 'value': format(level, 'f')})
        return points

    @staticmethod
    def _fraction_from_percent(value: object) -> str | None:
        if value in (None, ''):
            return None
        text = str(value).strip().replace('%', '').replace(',', '')
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        if not number.is_finite():
            return None
        return format(number / Decimal(100), 'f')

    @staticmethod
    def _number(value: object) -> str | None:
        if value in (None, ''):
            return None
        text = str(value).strip().replace(',', '').replace('$', '')
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        return format(number, 'f') if number.is_finite() and number > 0 else None

    @classmethod
    def _find_payload_value(cls, payload: object, keys: tuple[str, ...]) -> object:
        wanted = {key.lower() for key in keys}
        if isinstance(payload, dict):
            for key, value in payload.items():
                if str(key).lower() in wanted and value not in (None, ''):
                    return value
                found = cls._find_payload_value(value, keys)
                if found is not None:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = cls._find_payload_value(item, keys)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _iso_date(value: object) -> str | None:
        if value in (None, ''):
            return None
        text = str(value).strip()
        try:
            if '-' in text:
                return date.fromisoformat(text[:10]).isoformat()
            month, day, year = text.split('/')
            return date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _request_json(url: str) -> tuple[dict, bytes, int]:
        validate_safe_source_url(url)
        request = urllib.request.Request(url, headers={'Accept': 'application/json'}, method='GET')
        opener = urllib.request.build_opener(HttpsRedirectHandler())
        try:
            with opener.open(request, timeout=20) as response:
                final = urllib.parse.urlsplit(response.geturl())
                if final.scheme.lower() != 'https':
                    raise ProviderError('insecure_final_url_blocked')
                raw = response.read(20_000_001)
                if len(raw) > 20_000_000:
                    raise ProviderError('response_too_large')
                status = int(getattr(response, 'status', 200))
        except urllib.error.HTTPError as error:
            raise ProviderError(f'http_status_{error.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError('network_error') from None
        try:
            payload = json.loads(raw.decode('utf-8-sig'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError('invalid_json') from None
        if not isinstance(payload, dict):
            raise ProviderError('invalid_json')
        return payload, raw, status