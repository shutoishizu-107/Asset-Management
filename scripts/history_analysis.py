"""Parse order and execution exports separately; reconcile without adding them."""
import csv
import hashlib
import io
from collections import Counter, defaultdict
from datetime import datetime

from analyze_holdings import decode_csv, integer, normalize

ORDER_HEADER = ['発注日', '注文状況', 'ファンド名', '協会コード', '預り区分', '取引種別', 'コース', '注文金額', '手数料等', '税額', '約定単価', '約定数量']
TRADE_HEADER = ['約定日', '銘柄', '銘柄コード', '市場', '取引', '期限', '預り', '課税', '約定数量', '約定単価', '手数料/諸経費等', '税額', '受渡日', '受渡金額']


def day(value):
    return datetime.strptime(value, '%Y/%m/%d').date().isoformat()


def account_name(value):
    value = normalize(value)
    return value.removesuffix('預り')


def parse_export(path, kind):
    raw = path.read_bytes()
    text, encoding = decode_csv(raw)
    rows = list(csv.reader(io.StringIO(text), strict=True))
    header = ORDER_HEADER if kind == 'orders' else TRADE_HEADER
    title = '積立買付注文履歴' if kind == 'orders' else '約定履歴'
    if not rows or rows[0] != [title]:
        raise ValueError(f'Wrong history type: {path.name}')
    meta_i = next(i for i, r in enumerate(rows) if r and r[0] == '商品指定')
    meta = dict(zip(rows[meta_i], rows[meta_i + 1], strict=True))
    period = path.stem.split('_')[-2:]
    expected_start = datetime.strptime(meta['発注開始年月日' if kind == 'orders' else '約定開始年月日'], '%Y年%m月%d日').strftime('%Y%m%d')
    expected_end = datetime.strptime(meta['発注終了年月日' if kind == 'orders' else '約定終了年月日'], '%Y年%m月%d日').strftime('%Y%m%d')
    if len(period) == 2 and period != [expected_start, expected_end]:
        raise ValueError('Filename dates differ from export search period')
    if integer(meta['明細指定開始']) != 1 or integer(meta['明細指定終了']) != integer(meta['明細数']):
        raise ValueError('Partial export: download the entire selected period')
    start = rows.index(header) + 1
    records = []
    for index, row in enumerate(rows[start:], start + 1):
        if not row or all(not c for c in row):
            continue
        d = dict(zip(header, row, strict=True))
        if kind == 'orders':
            record = dict(date=day(d['発注日']), status=d['注文状況'], fund=normalize(d['ファンド名']),
                account=account_name(d['預り区分']), amount_yen=integer(d['注文金額']), fees_yen=integer(d['手数料等']),
                tax_yen=integer(d['税額']), nav_yen=integer(d['約定単価']), units=integer(d['約定数量']),
                association_code=d['協会コード'], order_type=d['取引種別'], course=d['コース'])
            if record['status'] == '完了' and (record['units'] <= 0 or record['nav_yen'] <= 0):
                raise ValueError('Completed order lacks execution quantity/price')
        else:
            trade_type = d['取引']
            if trade_type not in ('投信金額買付', '投信金額解約'):
                raise ValueError(f'Unsupported transaction: {trade_type}')
            record = dict(date=day(d['約定日']), settlement_date=day(d['受渡日']), fund=normalize(d['銘柄']),
                account=account_name(d['預り']), side='buy' if trade_type == '投信金額買付' else 'sell',
                amount_yen=integer(d['受渡金額']), fees_yen=integer(d['手数料/諸経費等']), tax_yen=integer(d['税額']),
                nav_yen=integer(d['約定単価']), units=integer(d['約定数量']), tax_status=d['課税'])
        record.update(source_file=path.name, source_line=index)
        if any(record[k] < 0 for k in ('amount_yen', 'fees_yen', 'tax_yen', 'units', 'nav_yen')):
            raise ValueError('Unexpected negative amount in history')
        records.append(record)
    if len(records) != integer(meta['明細数']):
        raise ValueError('History row count differs from metadata')
    # No transaction IDs: preserve duplicate rows and flag them, do not silently drop.
    signatures = [tuple((k, v) for k, v in r.items() if k not in ('source_file', 'source_line')) for r in records]
    duplicate_count = len(signatures) - len(set(signatures))
    return dict(records=records, metadata=meta, encoding=encoding, sha256=hashlib.sha256(raw).hexdigest(),
        source_file=path.name, duplicate_rows=duplicate_count)


def monthly_groups(orders, trades):
    results = {}
    for row in orders:
        month = row['date'][:7]
        r = results.setdefault(month, dict(month=month, completed_order_yen=0, failed_order_yen=0, buy_yen=0, sell_yen=0, net_purchase_yen=0))
        r['completed_order_yen' if row['status'] == '完了' else 'failed_order_yen'] += row['amount_yen']
    for row in trades:
        month = row['date'][:7]
        r = results.setdefault(month, dict(month=month, completed_order_yen=0, failed_order_yen=0, buy_yen=0, sell_yen=0, net_purchase_yen=0))
        r['buy_yen' if row['side'] == 'buy' else 'sell_yen'] += row['amount_yen']
        r['net_purchase_yen'] = r['buy_yen'] - r['sell_yen']
    return [results[k] for k in sorted(results)]


def reconcile(orders, trades):
    used = set()
    matches, unmatched = [], []
    for trade in trades:
        if trade['side'] != 'buy':
            continue
        candidates = []
        for i, order in enumerate(orders):
            if i in used or order['status'] != '完了':
                continue
            fields = ('fund', 'account', 'amount_yen', 'nav_yen', 'units')
            lag = (datetime.fromisoformat(trade['date']) - datetime.fromisoformat(order['date'])).days
            if all(trade[k] == order[k] for k in fields) and 0 <= lag <= 31:
                candidates.append(i)
        if len(candidates) == 1:
            index = candidates[0]
            used.add(index)
            matches.append(dict(order_line=orders[index]['source_line'], trade_line=trade['source_line'],
                order_date=orders[index]['date'], trade_date=trade['date'], fund=trade['fund'],
                account=trade['account'], amount_yen=trade['amount_yen'], units=trade['units']))
        else:
            unmatched.append(dict(**trade, match_candidates=len(candidates)))
    return matches, unmatched


def analyze_history(raw_dir, holdings, source_dir=None):
    # source_dir is an explicit staging override; normal runs only use raw/csv.
    source_dir = source_dir or raw_dir
    order_paths = list(source_dir.glob('積立買付注文履歴_????????_????????.csv'))
    trade_paths = list(source_dir.glob('約定履歴_????????_????????.csv'))
    if len(order_paths) != 1 or len(trade_paths) != 1:
        raise ValueError('Exactly one orders export and one trades export are required; do not combine overlapping exports')
    order_path, trade_path = order_paths[0], trade_paths[0]
    order_export = parse_export(order_path, 'orders')
    trade_export = parse_export(trade_path, 'trades')
    orders, trades = order_export['records'], trade_export['records']
    matches, unmatched = reconcile(orders, trades)
    statuses = []
    for status in sorted({r['status'] for r in orders}):
        part = [r for r in orders if r['status'] == status]
        statuses.append(dict(status=status, count=len(part), amount_yen=sum(r['amount_yen'] for r in part)))
    funds = {}
    annual = {}
    for trade in trades:
        for table, key in ((funds, trade['fund']), (annual, trade['date'][:4])):
            item = table.setdefault(key, dict(name=key, buy_yen=0, sell_yen=0, buy_count=0, sell_count=0))
            item[trade['side'] + '_yen'] += trade['amount_yen']
            item[trade['side'] + '_count'] += 1
    for item in list(funds.values()) + list(annual.values()):
        item['net_purchase_yen'] = item['buy_yen'] - item['sell_yen']
    current_units = {(account_name(h['account']), h['fund']): h['units'] for h in holdings}
    net_units = defaultdict(int)
    for trade in trades:
        net_units[trade['account'], trade['fund']] += trade['units'] * (1 if trade['side'] == 'buy' else -1)
    bridge = [dict(account=k[0], fund=k[1], current_units=current_units.get(k, 0),
        net_traded_units=net_units.get(k, 0), implied_opening_units=current_units.get(k, 0) - net_units.get(k, 0))
        for k in sorted(current_units.keys() | net_units.keys())]
    latest_month = max(r['date'][:7] for r in trades)
    latest_buys = [r for r in trades if r['side'] == 'buy' and r['date'].startswith(latest_month)]
    return dict(orders=order_export, trades=trade_export, statuses=statuses, monthly=monthly_groups(orders, trades),
        annual=list(annual.values()), by_fund=list(funds.values()), reconciliation=matches, unmatched_buys=unmatched,
        unit_bridge=bridge, latest_month=latest_month, latest_buys=latest_buys,
        buy_yen=sum(r['amount_yen'] for r in trades if r['side'] == 'buy'),
        sell_yen=sum(r['amount_yen'] for r in trades if r['side'] == 'sell'),
        buy_count=sum(r['side'] == 'buy' for r in trades), sell_count=sum(r['side'] == 'sell' for r in trades))
