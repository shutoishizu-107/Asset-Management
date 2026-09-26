"""Offline SBI fund-holdings analysis. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SUMMARY_HEADER = ['評価額 (円)', '評価損益 (円)', '評価損益 (率・%)', '前日比 (円)', '前日比 (率・%)']
DETAIL_HEADER = ['ファンド名', '積立設定中', '定期売却設定中', '保有口数 (口)', '売却注文中 (口)',
                 '基準価額 (円)', '取得単価 (円)', '評価額 (円)', '取得金額 (円)',
                 '評価損益 (円)', '評価損益 (率・%)', '評価損益 前日比 (円)', '評価損益 前日比 (率・%)']
MONEY_FIELDS = ('value_yen', 'cost_yen', 'gain_yen', 'day_change_yen')


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split())


def integer(value):
    # Amounts are exact integer yen; never silently truncate a decimal.
    if not re.fullmatch(r'[+-]?\d+', value):
        raise ValueError(f'Invalid integer: {value!r}')
    return int(value)


def percentage(numerator, denominator):
    if denominator == 0:
        return None
    return str((Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def decimal_string(value):
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f'Invalid percentage: {value}')
    return str(result)


def decode_csv(raw):
    for encoding in ('utf-8-sig', 'cp932'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError('CSV must be UTF-8 or CP932')


def parse_text(text):
    sections, holdings = {}, []
    section = mode = None
    for line, row in enumerate(csv.reader(io.StringIO(text), strict=True), 1):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) == 1:
            if row[0] == '保有状況-全体':
                section, expected = '全体', None
            else:
                match = re.fullmatch(r'保有状況-金額指定-(.+) \((\d+)件\)', row[0])
                if not match:
                    raise ValueError(f'Line {line}: unknown section {row[0]!r}')
                section, expected = match[1], int(match[2])
            if section in sections:
                raise ValueError(f'Duplicate section: {section}')
            sections[section] = {'expected_count': expected, 'summary': None}
            mode = None
            continue
        if section is None:
            raise ValueError(f'Line {line}: missing section')
        if row == SUMMARY_HEADER:
            mode = 'summary'
            continue
        if row == DETAIL_HEADER:
            if section == '全体':
                raise ValueError('Details cannot belong to overall summary')
            mode = 'detail'
            continue
        if mode == 'summary' and len(row) == 5:
            if sections[section]['summary'] is not None:
                raise ValueError(f'Duplicate summary: {section}')
            sections[section]['summary'] = dict(value_yen=integer(row[0]), gain_yen=integer(row[1]),
                gain_pct_csv=decimal_string(row[2]), day_change_yen=integer(row[3]), day_change_pct_csv=decimal_string(row[4]))
            mode = None
        elif mode == 'detail' and len(row) == 13:
            holding = dict(account=section, fund=normalize(row[0]), fund_original=row[0],
                accumulation_marker=row[1], periodic_sale_marker=row[2], units=integer(row[3]),
                sale_order_units=integer(row[4]), nav_yen=integer(row[5]), unit_cost_yen=integer(row[6]),
                value_yen=integer(row[7]), cost_yen=integer(row[8]), gain_yen=integer(row[9]),
                gain_pct_csv=decimal_string(row[10]), day_change_yen=integer(row[11]), day_change_pct_csv=decimal_string(row[12]))
            if any(holding[key] < 0 for key in ('units', 'sale_order_units', 'nav_yen', 'unit_cost_yen', 'value_yen', 'cost_yen')):
                raise ValueError(f'Line {line}: negative quantity or value')
            holdings.append(holding)
        else:
            raise ValueError(f'Line {line}: unexpected row or column count ({len(row)})')
    validate(holdings, sections)
    return holdings, sections


def totals(rows):
    return {key: sum(row[key] for row in rows) for key in MONEY_FIELDS}


def validate(holdings, sections):
    if not holdings or '全体' not in sections:
        raise ValueError('Missing holdings or overall summary')
    keys = [(h['account'], h['fund']) for h in holdings]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate account/fund detail')
    for h in holdings:
        if h['value_yen'] - h['cost_yen'] != h['gain_yen']:
            raise ValueError(f'Value minus cost differs from gain: {h["fund"]}')
    for account, info in sections.items():
        rows = holdings if account == '全体' else [h for h in holdings if h['account'] == account]
        if account != '全体' and len(rows) != info['expected_count']:
            raise ValueError(f'Detail count mismatch: {account}')
        if info['summary'] is None:
            raise ValueError(f'Missing summary: {account}')
        actual = totals(rows)
        for key in ('value_yen', 'gain_yen', 'day_change_yen'):
            if actual[key] != info['summary'][key]:
                raise ValueError(f'Summary mismatch: {account}/{key}')


def category(name):
    name = normalize(name)
    # These are product-name groupings, not underlying asset/geographic weights.
    known = {
        'eMAXIS Slim バランス(8資産均等型)': '8資産均等型',
        'SBI・V・S&P500インデックス・ファンド': 'S&P500系',
        'eMAXIS Slim 米国株式(S&P500)': 'S&P500系',
        'eMAXIS Slim 全世界株式(オール・カントリー)': 'オール・カントリー',
        'iFreeNEXT FANG+インデックス': 'FANG+',
        'SBI・iシェアーズ・インド株式インデックス・ファンド': 'インド株',
    }
    return known.get(name, 'その他')


def aggregate(holdings, field):
    groups = defaultdict(list)
    for holding in holdings:
        groups[holding[field]].append(holding)
    total = totals(holdings)['value_yen']
    results = []
    for name, rows in groups.items():
        item = dict(name=name, count=len(rows), **totals(rows))
        item.update(weight_pct=percentage(item['value_yen'], total), gain_pct=percentage(item['gain_yen'], item['cost_yen']))
        results.append(item)
    return sorted(results, key=lambda item: (-item['value_yen'], item['name']))


def load_snapshot(path):
    match = re.fullmatch(r'fundHoldings_(\d{14})\.csv', path.name, re.IGNORECASE)
    named = re.fullmatch(r'保有状況_(\d{8})_(\d{8})\.csv', path.name)
    manifest_path = path.parents[3] / 'docs/data_sources.json'
    source_info = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        source_info = manifest['files'].get('data/raw/csv/' + path.name, {})
    if match:
        timestamp = datetime.strptime(match[1], '%Y%m%d%H%M%S').isoformat()
    elif named:
        if named[1] != named[2]:
            raise ValueError('A holdings snapshot must have the same start and end date')
        date = datetime.strptime(named[1], '%Y%m%d').date().isoformat()
        timestamp = source_info.get('export_timestamp_inferred', date)
        if timestamp[:10] != date:
            raise ValueError('Manifest export date differs from snapshot filename')
    else:
        raise ValueError(f'Unexpected holdings filename: {path.name}')
    raw = path.read_bytes()
    if source_info.get('sha256') and hashlib.sha256(raw).hexdigest() != source_info['sha256']:
        raise ValueError('Raw file differs from recorded source hash; review before updating metadata')
    text, encoding = decode_csv(raw)
    holdings, sections = parse_text(text)
    for h in holdings:
        h['category'] = category(h['fund'])
    total = totals(holdings)
    total['gain_pct'] = percentage(total['gain_yen'], total['cost_yen'])
    return dict(schema_version=1, source_file=path.name, original_source_file=source_info.get('original_filename', path.name), source_sha256=hashlib.sha256(raw).hexdigest(),
        source_encoding=encoding, export_timestamp_inferred=timestamp, valuation_date=None,
        date_note='出力日時はファイル名から推定。評価基準日時・タイムゾーンはCSV本文に記載なし。',
        totals=total, holdings=holdings, accounts=aggregate(holdings, 'account'),
        funds=aggregate(holdings, 'fund'), categories=aggregate(holdings, 'category'),
        csv_sections=sections, validation='passed')


def contribution_projection(snapshot, assumptions):
    monthly = assumptions['monthly_contributions_yen']
    if any(type(value) is not int or value < 0 for value in monthly.values()):
        raise ValueError('Monthly contributions must be nonnegative integer yen')
    known = {row['name']: row['value_yen'] for row in snapshot['categories']}
    if set(monthly) - set(known):
        raise ValueError('Contribution category not found in holdings')
    result = []
    for months in (0, 12, 36, 60):
        total = snapshot['totals']['value_yen'] + months * sum(monthly.values())
        for name, current in known.items():
            value = current + months * monthly.get(name, 0)
            result.append(dict(months=months, category=name, value_yen=value, total_yen=total, weight_pct=percentage(value, total)))
    return result


def compare(current, previous):
    if previous is None:
        return None
    old = {(h['account'], h['fund']): h for h in previous['holdings']}
    new = {(h['account'], h['fund']): h for h in current['holdings']}
    rows = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key, {}), new.get(key, {})
        rows.append(dict(account=key[0], fund=key[1], status='added' if not before else 'removed' if not after else 'continued',
            **{field + '_delta': after.get(field, 0) - before.get(field, 0) for field in (*MONEY_FIELDS, 'units')}))
    return dict(previous_source=previous['source_file'], current_source=current['source_file'],
        previous_export_timestamp_inferred=previous['export_timestamp_inferred'],
        note='残高差分。入出金・売買を含むため運用リターンではありません。月次とは限りません。', rows=rows)


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def run(root, output_root=None, assumptions_path=None, history_source_dir=None, offline=False, refresh_external_data=False, month=None):
    if offline and refresh_external_data:
        raise ValueError('offline mode cannot be combined with external refresh')
    persist_cache = output_root is None or output_root.resolve() == root.resolve()
    output_root = output_root or root
    assumptions_path = assumptions_path or root / 'docs/analysis_assumptions.json'
    assumptions = json.loads(assumptions_path.read_text(encoding='utf-8-sig'))
    profile_path = root / 'config/profile.json'
    goals_path = root / 'config/goals.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8-sig'))
    goals = json.loads(goals_path.read_text(encoding='utf-8-sig'))
    paths = sorted((root / 'data/raw/csv').glob('保有状況_????????_????????.csv')) + sorted((root / 'data/raw/csv').glob('fundHoldings_*.csv'))
    if not paths:
        raise ValueError('No holdings snapshots in data/raw/csv')
    snapshot_by_time = {snapshot['export_timestamp_inferred']: snapshot for snapshot in (load_snapshot(path) for path in paths)}
    history_folder = root / 'data/history'
    if history_folder.is_dir():
        for snapshot_path in history_folder.glob('*_snapshot.json'):
            try:
                saved_snapshot = json.loads(snapshot_path.read_text(encoding='utf-8-sig'))
            except (OSError, json.JSONDecodeError):
                continue
            if saved_snapshot.get('schema_version') == 1 and saved_snapshot.get('validation') == 'passed':
                timestamp = saved_snapshot.get('export_timestamp_inferred')
                if timestamp:
                    snapshot_by_time.setdefault(timestamp, saved_snapshot)
    snapshots = sorted(snapshot_by_time.values(), key=lambda item: item['export_timestamp_inferred'])
    timestamps = [s['export_timestamp_inferred'] for s in snapshots]
    if len(set(timestamps)) != len(timestamps):
        raise ValueError('Duplicate export timestamp')
    if month is not None and not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('Month must use YYYY-MM format')
    month_snapshots = [item for item in snapshots if item['export_timestamp_inferred'][:7] == month] if month else snapshots
    if not month_snapshots:
        raise ValueError(f'No validated holdings snapshot available for month {month}')
    current = month_snapshots[-1]
    current_month = current['export_timestamp_inferred'][:7]
    previous = next((item for item in reversed(snapshots) if item['export_timestamp_inferred'] < current['export_timestamp_inferred']), None)
    comparison = compare(current, previous)
    projection = contribution_projection(current, assumptions)
    from history_analysis import analyze_history
    from diagnosis import diagnosis
    history = analyze_history(root / 'data/raw/csv', current['holdings'], history_source_dir)
    diagnostic = diagnosis(current, history)
    from cache.monthly_snapshot import MonthlySnapshotStore
    from providers.registry import collect_latest
    market_store = MonthlySnapshotStore(root)
    saved_market = market_store.latest(current_month, current['export_timestamp_inferred'][:10]) if not refresh_external_data else None
    if saved_market is not None:
        saved_metric_priorities = saved_market.get('metric_priority_level')
        if not saved_metric_priorities:
            registry_document = json.loads((root / 'sources.yaml').read_text(encoding='utf-8-sig'))
            saved_metric_priorities = registry_document.get('metric_priority_level', {})
        external_collection = {
            'fetched_at': saved_market['manifest']['snapshot_created_at'],
            'fetch_requested': False,
            'requests_made': 0,
            'public_records': saved_market['records'],
            'fund_evaluations': saved_market['fund_evaluations'],
            'metric_priority_level': saved_metric_priorities,
            'snapshot_path': str(saved_market['path'].relative_to(root)),
        }
    else:
        portfolio_weights = {}
        for holding in current['holdings']:
            portfolio_weights[holding['fund']] = portfolio_weights.get(holding['fund'], 0) + holding['value_yen']
        external_collection = collect_latest(
            root,
            persist=persist_cache,
            offline=offline,
            force_refresh=refresh_external_data,
            portfolio_weights=portfolio_weights,
        )
        if persist_cache:
            saved_market = market_store.write(
                current['export_timestamp_inferred'][:10],
                external_collection['public_records'],
                external_collection['fund_evaluations'],
                force_revision=refresh_external_data,
                metric_priority_level=external_collection.get('metric_priority_level', {}),
            )
            external_collection['snapshot_path'] = str(saved_market['path'].relative_to(root))
    tag = current['export_timestamp_inferred'].replace('-', '').replace(':', '').replace('T', '')
    day_label = current['export_timestamp_inferred'][:10].replace('-', '')
    om, tm = history['orders']['metadata'], history['trades']['metadata']
    period = lambda value: datetime.strptime(value, '%Y年%m月%d日').strftime('%Y%m%d')
    order_start, order_end = period(om['発注開始年月日']), period(om['発注終了年月日'])
    trade_start, trade_end = period(tm['約定開始年月日']), period(tm['約定終了年月日'])
    import calendar
    base_date = datetime.strptime(day_label, '%Y%m%d')
    future_year = base_date.year + 5
    future_label = base_date.replace(year=future_year, day=min(base_date.day, calendar.monthrange(future_year, base_date.month)[1])).strftime('%Y%m%d')
    spans = {
        'holdings': ('保有明細', day_label, day_label),
        'accounts': ('口座別集計', day_label, day_label),
        'funds': ('ファンド別集計', day_label, day_label),
        'categories': ('商品グループ別集計', day_label, day_label),
        'contribution_projection': ('現行積立配分試算', day_label, future_label),
        'orders': ('積立買付注文整形', order_start, order_end),
        'executions': ('約定履歴整形', trade_start, trade_end),
        'history_monthly': ('注文約定月次集計', min(order_start, trade_start), max(order_end, trade_end)),
        'order_execution_matches': ('注文約定照合', trade_start, trade_end),
        'unit_bridge': ('保有口数照合', trade_start, day_label),
    }
    if comparison:
        spans['comparison'] = ('保有状況差分', comparison['previous_export_timestamp_inferred'][:10].replace('-', ''), day_label)
    export_names = {name: '_'.join(parts) + '.csv' for name, parts in spans.items()}
    folders = {key: output_root / value for key, value in dict(processed='data/processed', history='data/history', monthly='reports/monthly', charts='reports/charts').items()}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    # Validate every input before writing output. Old snapshots are never used as inputs.
    for snapshot in snapshots:
        stamp = snapshot['export_timestamp_inferred'].replace('-', '').replace(':', '').replace('T', '')
        write_json(folders['history'] / f'{stamp}_snapshot.json', snapshot)
    for name in ('holdings', 'accounts', 'funds', 'categories'):
        write_csv(folders['processed'] / export_names[name], current[name])
    write_csv(folders['processed'] / export_names['contribution_projection'], projection)
    if comparison:
        write_csv(folders['processed'] / export_names['comparison'], comparison['rows'])
    handover = (root / 'docs/handover.md').read_bytes()
    run_info = dict(snapshot=current, comparison=comparison, projection=projection, assumptions=assumptions,
        profile=profile, goals=goals,
        assumptions_sha256=hashlib.sha256(assumptions_path.read_bytes()).hexdigest(),
        profile_sha256=hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        goals_sha256=hashlib.sha256(goals_path.read_bytes()).hexdigest(),
        handover_sha256=hashlib.sha256(handover).hexdigest(), snapshot_count=len(snapshots))
    public_records = [
        record for record in external_collection['public_records']
        if record.get('metric') != 'portfolio_overlap'
    ]
    run_info.update(
        history=history,
        diagnosis=diagnostic,
        csv_outputs=export_names,
        external_data={
            'fetched_at': external_collection['fetched_at'],
            'fetch_requested': external_collection['fetch_requested'],
            'requests_made': external_collection['requests_made'],
            'monthly_snapshot_path': external_collection.get('snapshot_path'),
            'available_count': sum(record['status'] == 'available' for record in public_records),
            'unavailable_count': sum(record['status'] == 'unavailable' for record in public_records),
            'records': public_records,
            'metric_priority_level': external_collection.get('metric_priority_level', {}),
        },
    )
    for name, rows in {'orders': history['orders']['records'], 'executions': history['trades']['records'], 'history_monthly': history['monthly'], 'order_execution_matches': history['reconciliation'], 'unit_bridge': history['unit_bridge']}.items():
        write_csv(folders['processed'] / export_names[name], rows)
    write_json(folders['processed'] / f'{tag}_analysis.json', run_info)
    from reporting import render_dashboard, render_report, render_charts, render_index
    (folders['monthly'] / f'資産分析_{tag[:6]}.html').write_text(render_report(run_info, tag), encoding='utf-8')
    (folders['charts'] / f'資産配分_{tag[:6]}.html').write_text(render_charts(run_info), encoding='utf-8')
    monthly_reports = sorted(path.name for path in folders['monthly'].glob('資産分析_*.html'))
    chart_reports = sorted(path.name for path in folders['charts'].glob('資産配分_*.html'))
    (output_root / 'index.html').write_text(render_dashboard(run_info, tag), encoding='utf-8')
    (output_root / 'reports/index.html').write_text(render_index(run_info, tag, monthly_reports, chart_reports), encoding='utf-8')
    return run_info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--assumptions', type=Path)
    parser.add_argument('--history-source-dir', type=Path)
    parser.add_argument('--month', help='Report month as YYYY-MM; must match an available holdings snapshot')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--refresh-external-data', '--fetch-external-data', dest='refresh_external_data', action='store_true', help='Ignore cache TTL and refresh enabled, approved providers')
    mode.add_argument('--offline', action='store_true', help='Do not access external providers; use only cache values')
    args = parser.parse_args()
    root = args.root.resolve()
    output_root = args.output_root.resolve() if args.output_root else None
    info = run(root, output_root, args.assumptions, args.history_source_dir, args.offline, args.refresh_external_data, args.month)
    print(json.dumps({'status': 'ok', 'totals': info['snapshot']['totals'], 'categories': info['snapshot']['categories'],
        'snapshot_count': info['snapshot_count'], 'external_data': info['external_data']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
