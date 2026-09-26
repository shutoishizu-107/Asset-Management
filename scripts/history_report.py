"""HTML sections for execution evidence and provisional portfolio recommendations."""
from datetime import date, datetime
from html import escape
from reporting import yen


def simple_table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(str(c)) + '</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def render_history(info):
    h = info['history']
    om, tm = h['orders']['metadata'], h['trades']['metadata']
    body = '<section id="history"><h2>積立注文・買付実績</h2>'
    body += '<p>注文履歴の検索期間：' + escape(om['発注開始年月日'] + ' ～ ' + om['発注終了年月日']) + '。約定履歴の検索期間：' + escape(tm['約定開始年月日'] + ' ～ ' + tm['約定終了年月日']) + '。</p>'
    body += '<h3>完了した積立買付注文</h3>' + simple_table(['注文状況', '件数', '注文金額の合計（円）'], [[r['status'], r['count'], yen(r['amount_yen'])] for r in h['statuses'] if r['status'] == '完了'])
    body += '<h3>検索期間内の買付実績</h3>'
    body += f'<p>買付 {h["buy_count"]}件・{yen(h["buy_yen"])}円。受渡金額ベースの購入額であり、運用利益ではありません。</p>'
    body += simple_table(['年（約定日基準）', '買付額（円）'], [[r['name'], yen(r['buy_yen'])] for r in sorted(h['annual'], key=lambda r: r['name'])])
    body += '<p class="muted">検索期間が年の途中で始まる・終わる年は部分年です。年額として単純比較しません。</p>'
    body += simple_table(['ファンド', '買付額（円）'], [[r['name'], yen(r['buy_yen'])] for r in h['by_fund'] if r['buy_yen']])
    body += '<h3>注文と約定の照合</h3>'
    body += f'<p>買付 {h["buy_count"]}件中 {len(h["reconciliation"])}件が、ファンド・口座・金額・単価・口数の一致と、発注日から約定日まで0～31日という条件で一意に対応しました。未照合は {len(h["unmatched_buys"])}件です。取引IDによる照合ではないため、対応候補として保存しています。</p>'
    body += '<p>注文日と約定日は月をまたぐことがあります。注文履歴と約定履歴は加算せず、実際の購入の集計には約定履歴を使用します。</p>'
    if h['unmatched_buys']:
        body += '<details><summary>未照合の買付</summary>' + simple_table(['約定日', 'ファンド', '受渡額（円）', '候補数'], [[r['date'], r['fund'], yen(r['amount_yen']), r['match_candidates']] for r in h['unmatched_buys']]) + '</details>'
    body += '<p>未照合の買付は積立以外の買付である可能性もあります。未照合という理由だけでエラーと断定せず、約定済みとして集計しています。</p>'
    body += '<h3>直近月の買付</h3><p>' + escape(h['latest_month']) + ' の約定履歴で確認した内訳です。将来の積立設定そのものを保証するものではありません。</p>'
    body += simple_table(['ファンド', '口座', '買付額（円）'], [[r['fund'], r['account'], yen(r['amount_yen'])] for r in h['latest_buys']])
    body += '<details><summary>月別の完了注文と買付実績</summary>' + simple_table(['月', '完了注文額（発注月）', '買付額（約定月）'], [[r['month'], yen(r['completed_order_yen']), yen(r['buy_yen'])] for r in h['monthly']]) + '<p class="muted">約定履歴の検索期間外の0は、取引がなかったという意味ではありません。</p></details>' 

    body += '<h3>履歴の原本</h3><ul>'
    for key in ('orders', 'trades'):
        item = h[key]
        body += '<li><a href="../../data/raw/csv/' + escape(item['source_file'], quote=True) + '">' + escape(item['source_file']) + '</a> (' + escape(item['encoding']) + ')<div class="mono">SHA-256: ' + item['sha256'] + '</div></li>'
    body += '</ul>'
    duplicates = h['orders']['duplicate_rows'] + h['trades']['duplicate_rows']
    body += f'<p class="muted">同一内容の重複行検出：{duplicates}件。履歴ファイル全体の網羅性は、検索期間・商品指定・口座範囲に依存します。</p></section>'
    return body


def goal_projection(start_value, as_of, annual_return_pct, raise_at_30, goal):
    current_date = datetime.strptime(as_of[:10], '%Y-%m-%d').date()
    month = date(current_date.year + (current_date.month == 12), current_date.month % 12 + 1, 1)
    target_month = date(goal['birth_year'] + goal['target_age'], goal['birth_month'], 1)
    monthly_rate = (1 + annual_return_pct / 100) ** (1 / 12) - 1
    value = float(start_value)
    contributions = 0
    months = 0
    while month < target_month:
        age = month.year - goal['birth_year'] - (month.month < goal['birth_month'])
        steps = goal['contributions_yen']
        if age < 26:
            monthly = steps['through_age_25']
        elif age < 30:
            monthly = steps['age_26_to_29']
        else:
            monthly = steps['age_30_plus_tentative'] if raise_at_30 else steps['age_30_plus_base']
        value = value * (1 + monthly_rate) + monthly
        contributions += monthly
        months += 1
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return {'value_yen': round(value), 'contributions_yen': contributions, 'months': months}


def required_goal_return(start_value, as_of, raise_at_30, goal):
    low, high = 0.0, 100.0
    for _ in range(64):
        middle = (low + high) / 2
        if goal_projection(start_value, as_of, middle, raise_at_30, goal)['value_yen'] < goal['target_yen']:
            low = middle
        else:
            high = middle
    return high


def nisa_lifetime_exhaustion(as_of, goal, nisa, raise_at_30=False):
    current_date = datetime.strptime(as_of[:10], '%Y-%m-%d').date()
    month = date(current_date.year + (current_date.month == 12), current_date.month % 12 + 1, 1)
    target_month = date(goal['birth_year'] + goal['target_age'], goal['birth_month'], 1)
    remaining = nisa['lifetime']['total']['remaining_yen']
    months = 0
    while month < target_month:
        age = month.year - goal['birth_year'] - (month.month < goal['birth_month'])
        if age < 26:
            plan_key = 'current'
        elif age < 30 or not raise_at_30:
            plan_key = 'age_26_plus'
        else:
            plan_key = 'age_30_plus_tentative'
        monthly = sum(nisa['monthly_contributions_yen'][plan_key][bucket] for bucket in ('tsumitate', 'growth'))
        months += 1
        if monthly >= remaining:
            return {
                'month': month.strftime('%Y-%m'),
                'age_years': age,
                'months_from_snapshot': months,
                'nisa_yen': remaining,
                'taxable_yen': monthly - remaining,
            }
        remaining -= monthly
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return None


__all__ = ['render_history', 'goal_projection', 'required_goal_return', 'nisa_lifetime_exhaustion']


