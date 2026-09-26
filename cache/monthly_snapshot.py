"""Immutable per-date external data snapshots for reproducible report regeneration."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


INDEX_METRICS = {'benchmark', 'index_composition', 'index_region_weights', 'index_sector_weights', 'methodology'}
MACRO_METRICS = {'fed_funds_rate', 'treasury_yields', 'cpi', 'unemployment', 'recession_indicators', 'macro'}
NISA_METRICS = {'nisa_eligibility', 'nisa_rules'}
CATEGORIES = ('funds', 'indexes', 'macro', 'nisa')


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _category(record: dict[str, Any]) -> str:
    metric = record.get('metric')
    if metric in INDEX_METRICS:
        return 'indexes'
    if metric in MACRO_METRICS:
        return 'macro'
    if metric in NISA_METRICS:
        return 'nisa'
    return 'funds'


class MonthlySnapshotStore:
    def __init__(self, root: Path):
        self.root = root / 'data' / 'market'

    def latest(self, month: str, as_of_date: str | None = None) -> dict[str, Any] | None:
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError('month must be YYYY-MM')
        try:
            year, month_number = (int(part) for part in month.split('-'))
            date(year, month_number, 1)
        except ValueError:
            raise ValueError('month must be a valid YYYY-MM') from None
        if as_of_date is not None:
            try:
                requested_date = date.fromisoformat(as_of_date)
            except ValueError:
                raise ValueError('as_of_date must be an ISO calendar date') from None
            if requested_date.strftime('%Y-%m') != month:
                raise ValueError('as_of_date must belong to month')
            day_folders = [self.root / requested_date.isoformat()]
        else:
            day_folders = sorted(self.root.glob(month + '-[0-9][0-9]'))
        manifests = []
        for day_folder in day_folders:
            if not day_folder.is_dir():
                continue
            manifests.extend(day_folder.glob('manifest.json'))
            manifests.extend(day_folder.glob('revisions/*/manifest.json'))
        candidates = []
        for manifest_path in manifests:
            loaded = self._load_manifest(manifest_path)
            if loaded:
                candidates.append((loaded['manifest']['snapshot_created_at'], loaded))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    def write(self, as_of_date: str, records: list[dict[str, Any]], fund_evaluations: list[dict[str, Any]], force_revision: bool = False) -> dict[str, Any]:
        try:
            report_date = date.fromisoformat(as_of_date[:10])
        except (TypeError, ValueError):
            raise ValueError('snapshot as_of_date must be an ISO calendar date') from None
        month = report_date.strftime('%Y-%m')
        existing = self.latest(month, report_date.isoformat())
        if existing and not force_revision:
            return existing

        created = _utc_now()
        stamp = created.strftime('%Y%m%dT%H%M%S%fZ')
        base = self.root / report_date.isoformat()
        target = base / 'revisions' / stamp if base.exists() else base
        if target.exists():
            raise FileExistsError('monthly snapshots are immutable; target already exists')

        categorized = {name: [] for name in CATEGORIES}
        for record in records:
            categorized[_category(record)].append(record)
        file_names = {name: f'{name}.json' for name in CATEGORIES}
        manifest = {
            'schema_version': 1,
            'snapshot_id': stamp,
            'report_month': month,
            'as_of_date': report_date.isoformat(),
            'snapshot_created_at': created.isoformat(timespec='microseconds'),
            'snapshot_type': 'external_data_public_projection',
            'files': file_names,
            'record_counts': {name: len(values) for name, values in categorized.items()},
            'fund_evaluations': fund_evaluations,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f'.tmp-{stamp}'
        try:
            temporary.mkdir()
            for name, values in categorized.items():
                (temporary / file_names[name]).write_text(json.dumps({
                    'schema_version': 1,
                    'category': name,
                    'report_month': month,
                    'as_of_date': report_date.isoformat(),
                    'records': values,
                }, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
            (temporary / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
            temporary.replace(target)
        except Exception:
            if temporary.exists():
                for item in temporary.iterdir():
                    item.unlink()
                temporary.rmdir()
            raise
        return {'manifest': manifest, 'records': records, 'path': target}

    @staticmethod
    def _load_manifest(manifest_path: Path) -> dict[str, Any] | None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict):
                return None
            records = []
            for category in CATEGORIES:
                file_name = manifest['files'].get(category)
                if not file_name or Path(file_name).name != file_name:
                    return None
                document = json.loads((manifest_path.parent / file_name).read_text(encoding='utf-8'))
                if document.get('schema_version') != 1 or document.get('category') != category:
                    return None
                category_records = document.get('records')
                if not isinstance(category_records, list):
                    return None
                records.extend(category_records)
            return {
                'manifest': manifest,
                'records': records,
                'fund_evaluations': manifest.get('fund_evaluations', []),
                'path': manifest_path.parent,
            }
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            return None