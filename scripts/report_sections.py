"""Sections for the goal-first monthly investment report."""
import re
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from datetime import date

from history_report import (
    goal_projection,
    nisa_lifetime_exhaustion,
    required_goal_return,
    simple_table,
)
from reporting import bars, charts_body, data_table, header, pct, yen


def _month_label(value):
    year, month = (int(part) for part in value.split('-'))
    return f'{year}年{month}月'


def _short_yen(value):
    if value == 100000000:
        return '1億円'
    return yen(value) + '円'


def _card(label, value, unit=''):
    return '<div class="card"><div class="label">' + escape(label) + '</div><div class="number">' + escape(str(value)) + ('<span class="unit">' + escape(unit) + '</span>' if unit else '') + '</div></div>'


def render_executive_summary(info):
    snapshot, assumptions, diagnosis = info['snapshot'], info['assumptions'], info['diagnosis']
    nisa = assumptions['nisa_snapshot']
    total = snapshot['totals']['value_yen']
    stock = next(row for row in diagnosis['asset_estimate'] if row['name'] == '株式（概算）')
    goal = {**info['profile'], **info['goals']}
    target = date(goal['birth_year'] + goal['target_age'], goal['birth_month'], 1)
    source_date = date.fromisoformat(snapshot['export_timestamp_inferred'][:10])
    months_left = (target.year - source_date.year) * 12 + target.month - source_date.month
    years_left, extra_months = divmod(months_left, 12)
    current_monthly = sum(nisa['monthly_contributions_yen']['current'].values())
    cards = [
        _card('現在の投資信託評価額', yen(total), '円'),
        _card('35歳の目標', _short_yen(goal['target_yen'])),
        _card('現在の月積立設定', yen(current_monthly), '円 / 月'),
        _card('株式比率（概算）', pct(stock['weight_pct'])),
        _card('生涯NISA残り（9/25）', yen(nisa['lifetime']['total']['remaining_yen']), '円'),
        _card('目標まで', f'{years_left}年{extra_months}か月'),
    ]
    body = '<div class="cards">' + ''.join(cards) + '</div>'
    body += '<section id="summary"><h2>Executive Summary</h2>'
    body += '<p><strong>現在の投資信託評価額は ' + yen(total) + '円。</strong>35歳となる2038年1月に金融資産1億円を目指します。現在23歳で、月15万円から26歳ごろに月20万円へ増額する想定です。</p>'
    body += '<p>株式比率は8資産均等型の基本配分と他ファンド株式扱いによる概算で約' + pct(stock['weight_pct']) + '。S&amp;P500系が48.04%、FANG+が11.07%を占め、オルカン内の米国株もあります。正確な地域・銘柄重複率は未確認です。</p>'
    body += '<h3>今回の検討方針</h3><ol class="summary-points"><li>オルカンを長期コア候補にする</li><li>S&amp;P500は米国への意図的な上乗せとして分ける</li><li>既存FANG+と新規積立を別の判断にする</li><li>NASDAQ100・S&amp;P1000・除く米国・Goldを役割別に比較する</li><li>利用可能なNISA枠は長期コアを優先候補とし、枠対象を商品ごとに確認する</li></ol>'
    base_return = required_goal_return(total, snapshot['export_timestamp_inferred'], False, goal)
    raised_return = required_goal_return(total, snapshot['export_timestamp_inferred'], True, goal)
    body += '<p class="notice"><strong>最大の論点：</strong>この積立条件では、35歳で1億円に届く計算上の一定年率は約' + f'{base_return:.2f}%' + '（26歳以降20万円）または約' + f'{raised_return:.2f}%' + '（30歳から25万円）です。「高リターン商品を増やす」だけでなく、積立額と運用期間も併せて検討します。</p>'
    freshness = info.get('external_data', {}).get('records', [])
    important = [record for record in freshness if record.get('metric') in {'expense_ratio', 'total_expense_ratio', 'aum', 'holdings', 'benchmark', 'price_history'}]
    stale_count = sum(record.get('freshness_status') == 'stale' for record in important)
    unavailable_count = sum(record.get('freshness_status', 'unavailable') == 'unavailable' for record in important)
    if stale_count or unavailable_count:
        body += '<p class="notice"><strong>外部データの鮮度：</strong>重要指標 ' + str(stale_count) + '件がstale、' + str(unavailable_count) + '件がunavailableです。現在値の確認が必要な判断は保留し、不足データを推測で補いません。</p>'
    body += '<p class="muted">出発点は投資信託評価額で、銀行残高は含みません。ジュニアNISAを含み、本人への帰属が未確認です。金融資産全体の確定値としては扱いません。</p></section>'
    return body


def render_goal_tracker(info):
    snapshot, assumptions = info['snapshot'], info['assumptions']
    goal = {**info['profile'], **info['goals']}
    nisa = assumptions['nisa_snapshot']
    start_value = snapshot['totals']['value_yen']
    as_of = snapshot['export_timestamp_inferred']
    target = date(goal['birth_year'] + goal['target_age'], goal['birth_month'], 1)
    current_label = as_of[:7].replace('-', '/')
    target_label = f'{target.year}/{target.month:02d}'
    steps = goal['contributions_yen']
    body = '<section id="goal"><h2>Goal Tracker：35歳で1億円までの現在地</h2>'
    body += '<ol class="timeline"><li><span>' + f'{goal["birth_year"]}/{goal["birth_month"]:02d}' + '</span><strong>誕生</strong><small>23～25歳：月' + yen(steps['through_age_25']) + '円</small></li>'
    body += '<li class="current"><span>' + escape(current_label) + '</span><strong>現在・23歳</strong><small>投信 ' + yen(start_value) + '円<br>月' + yen(sum(nisa['monthly_contributions_yen']['current'].values())) + '円</small></li>'
    body += '<li><span>' + f'{goal["birth_year"] + 26}/01' + '</span><strong>26歳ごろ</strong><small>月' + yen(steps['age_26_to_29']) + '円へ増額想定</small></li>'
    body += '<li class="target"><span>' + escape(target_label) + '</span><strong>35歳・目標</strong><small>' + escape(_short_yen(goal['target_yen'])) + '</small></li></ol>'
    body += '<p>30歳以降の月25万円は検討中の比較ケースです。月20万円を継続するケースと分けて示します。</p>'
    body += '<p class="muted">毎月末に積立、年率は実効年率から月率へ換算して月次複利で試算。税・費用なし、価格が一定率で変化する単純モデルです。1億円の達成見込みや収益予測ではありません。</p>'
    rows = []
    for rate in goal['annual_return_scenarios_pct']:
        base = goal_projection(start_value, as_of, rate, False, goal)['value_yen']
        raised = goal_projection(start_value, as_of, rate, True, goal)['value_yen']
        rows.append([f'{rate}%', yen(base) + '円', yen(goal['target_yen'] - base) + '円', yen(raised) + '円', yen(goal['target_yen'] - raised) + '円'])
    base_required = required_goal_return(start_value, as_of, False, goal)
    raised_required = required_goal_return(start_value, as_of, True, goal)
    body += simple_table(['想定年率', '月20万円継続・35歳時点', '1億円との差', '30歳から月25万円・35歳時点', '1億円との差'], rows)
    body += '<h3>1億円に届くために必要な一定年率（計算値）</h3>'
    body += simple_table(['積立条件', '必要年率', '目標額'], [['26歳以降 月20万円', f'{base_required:.2f}%', yen(goal['target_yen']) + '円'], ['30歳から月25万円（検討中）', f'{raised_required:.2f}%', yen(goal['target_yen']) + '円']])
    principal = goal_projection(start_value, as_of, 0, False, goal)
    raised_principal = goal_projection(start_value, as_of, 0, True, goal)
    six_percent_base = goal_projection(start_value, as_of, 6, False, goal)['value_yen']
    six_percent_raised = goal_projection(start_value, as_of, 6, True, goal)['value_yen']
    body += '<p><strong>運用率と積立額の比較：</strong>0%の場合の35歳時点は月20万円継続で ' + yen(principal['value_yen']) + '円、25万円案で ' + yen(raised_principal['value_yen']) + '円。年率6%を固定した同一モデルでは、25万円案が約' + yen(six_percent_raised - six_percent_base) + '円上回ります。</p>'
    body += '<p class="muted">初期評価額にはジュニアNISAが含まれます。名義・本人帰属、生活防衛資金、近い将来の支出は未確認。出力日と価格評価基準日時も同一とは限りません。</p></section>'
    return body


def render_current_portfolio(info):
    snapshot, diagnosis = info['snapshot'], info['diagnosis']
    total = snapshot['totals']
    groups = {row['name']: row for row in snapshot['categories']}
    body = '<section id="portfolio"><h2>現在のポートフォリオ</h2><h3>総額・商品グループ・口座</h3>'
    body += '<div class="portfolio-charts">' + charts_body(snapshot) + '</div>'
    body += '<p>投資信託評価額 ' + yen(total['value_yen']) + '円、取得金額 ' + yen(total['cost_yen']) + '円、評価損益 ' + yen(total['gain_yen'], True) + '円（' + pct(total['gain_pct']) + '）。銀行預金は含みません。</p>'
    body += '<h3>ファンド別構成と損益</h3>' + data_table(snapshot['funds'], 'ファンド', total)
    body += '<p class="muted">取得金額は現在保有分の簿価で、累計入金額ではありません。構成比は投資信託評価額に対する割合です。</p>'
    body += '<h3>口座別の現在残高</h3>' + data_table(snapshot['accounts'], '口座区分', total)
    body += '<h3>商品グループで見える集中</h3>'
    body += simple_table(['商品グループ', '評価額', '構成比', '読み方'], [
        ['S&P500系', yen(groups['S&P500系']['value_yen']) + '円', pct(groups['S&P500系']['weight_pct']), '米国大型株指数の商品群'],
        ['FANG+', yen(groups['FANG+']['value_yen']) + '円', pct(groups['FANG+']['weight_pct']), '大型成長10社への集中商品'],
        ['オール・カントリー', yen(groups['オール・カントリー']['value_yen']) + '円', pct(groups['オール・カントリー']['weight_pct']), '内部の国別比率は今回未取得'],
        ['8資産均等型', yen(groups['8資産均等型']['value_yen']) + '円', pct(groups['8資産均等型']['weight_pct']), '株式・債券・REITを含むバランス型'],
    ])
    estimates = [[row['name'], yen(int(Decimal(row['value_yen']))) + '円', pct(row['weight_pct'])] for row in diagnosis['asset_estimate']]
    body += '<h3>資産クラス（モデルによる概算）</h3>' + simple_table(['資産クラス', '概算額', '投信全体比'], estimates)
    body += '<p class="muted">8資産均等型の基本配分を適用し、その他のファンドを株式扱いした推計です。ファンド内部の当日組入比率や実質的な地域配分ではありません。</p></section>'
    return body


def render_portfolio_diagnosis(info):
    snapshot, diagnosis, assumptions = info['snapshot'], info['diagnosis'], info['assumptions']
    groups = {row['name']: row for row in snapshot['categories']}
    stock = next(row for row in diagnosis['asset_estimate'] if row['name'] == '株式（概算）')
    bonds = next(row for row in diagnosis['asset_estimate'] if row['name'] == '債券（概算）')
    reits = next(row for row in diagnosis['asset_estimate'] if row['name'] == 'REIT（概算）')
    monthly = assumptions['monthly_contributions_yen']
    body = '<section id="diagnosis"><h2>現在のポートフォリオ診断</h2>'
    rows = [
        ['集中リスク', 'S&P500系 ' + pct(groups['S&P500系']['weight_pct']) + '、FANG+ ' + pct(groups['FANG+']['weight_pct']) + '。', 'オルカン内の米国大型株も重なる可能性があるが、組入明細がないため重複率・実質米国比率は算出不可。'],
        ['分散', '8資産均等型 ' + pct(groups['8資産均等型']['weight_pct']) + '。概算株式 ' + pct(stock['weight_pct']) + '、債券 ' + pct(bonds['weight_pct']) + '、REIT ' + pct(reits['weight_pct']) + '。', '現金と同じ安全資産ではない。基礎配分によるモデル推計。'],
        ['コスト', '現在保有ファンドの信託報酬・総経費率は保有CSVに含まれない。', '確認済みの現行費用比較がないため、候補表の概算値を保有中ファンドへ流用しない。'],
        ['積立方針', 'S&P500系・オール・カントリー・FANG+に各月' + yen(monthly['S&P500系']) + '円。', '新規積立の3分の1がFANG+に向かう設定。既存残高の比率とは分けて見直す。'],
    ]
    body += simple_table(['テーマ', '確認できる事実', '分析・未確認事項'], rows)
    body += '<p class="notice">保有額・損益はCSV確認値、株式/債券/REITはモデル概算、重複率・地域比率・現保有商品の確認済み費用は未確認です。これらを同じ確度の数値として扱いません。</p></section>'
    return body


def _progress(label, item, color):
    used, limit, remaining = item['used_yen'], item['limit_yen'], item['remaining_yen']
    ratio = min(100.0, used / limit * 100) if limit else 0
    return '<div class="nisa-progress"><div class="progress-head"><strong>' + escape(label) + '</strong><span>' + yen(used) + '円 / ' + yen(limit) + '円 (' + f'{ratio:.1f}%' + ')・残り ' + yen(remaining) + '円</span></div><div class="progress-track" role="progressbar" aria-label="' + escape(label, quote=True) + '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + f'{ratio:.1f}' + '"><div class="progress-fill" style="width:' + f'{ratio:.1f}' + '%;background:' + color + '"></div></div></div>'


def render_nisa_strategy(info):
    snapshot, assumptions = info['snapshot'], info['assumptions']
    nisa = assumptions['nisa_snapshot']
    goal = {**info['profile'], **info['goals']}
    body = '<section id="nisa"><h2>NISA戦略・利用状況</h2><p>利用額はSBI画面の2026-09-25転記値です。保有額や注文履歴から累計額を逆算していません。</p>'
    body += '<div class="two"><div><h3>2026年 年間投資枠</h3>'
    body += _progress('成長投資枠', nisa['annual']['growth'], '#246bce')
    body += _progress('つみたて投資枠', nisa['annual']['tsumitate'], '#16a58b')
    body += _progress('年間合計', nisa['annual']['total'], '#5c718c')
    body += '</div><div><h3>生涯投資枠</h3>'
    body += _progress('生涯投資枠', nisa['lifetime']['total'], '#246bce')
    body += _progress('うち成長投資枠', nisa['lifetime']['growth_sub_limit'], '#16a58b')
    life = nisa['lifetime']['total']
    body += '<p><strong>生涯枠消化率 ' + f'{life["used_yen"] / life["limit_yen"] * 100:.1f}' + '%、残り ' + yen(life['remaining_yen']) + '円。</strong></p></div></div>'
    current = nisa['monthly_contributions_yen']['current']
    age26 = nisa['monthly_contributions_yen']['age_26_plus']
    age30 = nisa['monthly_contributions_yen']['age_30_plus_tentative']
    body += '<h3>積立設定と枠の使い方</h3>'
    body += simple_table(['段階', 'つみたて投資枠/月', '成長投資枠/月', '合計/月', '年間換算'], [
        ['現在', yen(current['tsumitate']) + '円', yen(current['growth']) + '円', yen(sum(current.values())) + '円', yen(sum(current.values()) * 12) + '円'],
        ['26歳以降（想定）', yen(age26['tsumitate']) + '円', yen(age26['growth']) + '円', yen(sum(age26.values())) + '円', yen(sum(age26.values()) * 12) + '円'],
        ['30歳以降（25万円案・仮置き）', yen(age30['tsumitate']) + '円', yen(age30['growth']) + '円', yen(age30['tsumitate'] + age30['growth']) + '円', yen((age30['tsumitate'] + age30['growth']) * 12) + '円'],
    ])
    body += '<p>現在の月15万円はつみたて10万円＋成長5万円。26歳以降の想定20万円はつみたて10万円＋成長10万円です。いずれも年間合計360万円の範囲内ですが、投資先ごとの対象枠・販売会社の積立対応は別途確認します。</p>'
    body += '<h3>生涯枠の到達時期シナリオ</h3>'
    base = nisa_lifetime_exhaustion(nisa['source_date'], goal, nisa)
    raised = nisa_lifetime_exhaustion(nisa['source_date'], goal, nisa, True)
    body += simple_table(['積立条件', '残枠が尽きる月（推計）', '到達年齢', '到達月の特定口座回し'], [
        ['23～25歳15万円、その後20万円', _month_label(base['month']), f'{base["age_years"]}歳', yen(base['taxable_yen']) + '円'],
        ['30歳から25万円に増額（仮）', _month_label(raised['month']), f'{raised["age_years"]}歳', yen(raised['taxable_yen']) + '円'],
    ])
    body += '<p class="muted">画面の生涯残り枠から、将来積立をNISA対象商品で全額継続する単純計算です。対象外商品、将来の売却による枠再利用、他金融機関利用、積立変更は含みません。NISA枠を使い切った後も積立を続ける場合、超過分を特定口座に回す想定です。</p>'
    accounts = {row['name']: row for row in snapshot['accounts']}
    nisa_accounts = [accounts[name] for name in ('NISA (つみたて)預り', 'NISA (成長)預り') if name in accounts]
    body += '<h3>保有CSVにある新NISA口座の現在残高（9/26出力）</h3>'
    body += simple_table(['口座', '評価額', '取得金額', '評価損益'], [[row['name'], yen(row['value_yen']) + '円', yen(row['cost_yen']) + '円', yen(row['gain_yen'], True) + '円'] for row in nisa_accounts])
    body += '<p class="muted">現在残高の取得金額は年間または生涯利用額ではありません。生涯の最大利用可能額はSBI画面の転記を正として表示しています。</p></section>'
    return body


_PORTFOLIO_ALLOCATION = [
    {'role': '全世界株コア', 'genres': ('全世界株式',), 'weight_pct': 70, 'purpose': '長期保有の中心。'},
    {'role': '小型株・バリュー', 'genres': ('米国小型株',), 'weight_pct': 15, 'purpose': '小型株枠。バリュー特性は候補ごとに要確認。'},
    {'role': '金', 'genres': ('金',), 'weight_pct': 10, 'purpose': '株式以外の分散枠。'},
    {'role': '成長株サテライト', 'genres': ('米国成長株',), 'weight_pct': 5, 'purpose': '成長株への限定的な上乗せ。'},
]



_METRIC_LABELS = {
    'fund_name': 'ファンド名',
    'ticker': 'ティッカー',
    'asset_class': '資産クラス',
    'role_category': '役割',
    'benchmark': 'ベンチマーク',
    'expense_ratio': '経費率',
    'expense_ratio_bps': '経費率（bps）',
    'total_expense_ratio': '総経費率',
    'aum': '純資産総額',
    'fund_age': '運用年数',
    'fund_flow_1m': '1か月資金流入',
    'fund_flow_1y': '1年資金流入',
    'return_1y': '1年リターン',
    'return_3y_annualized': '3年年率リターン',
    'return_5y_annualized': '5年年率リターン',
    'return_10y_annualized': '10年年率リターン',
    'annualized_return': '年率リターン',
    'volatility': 'ボラティリティ',
    'max_drawdown': '最大下落率',
    'sharpe_ratio': 'シャープレシオ',
    'tracking_difference': 'トラッキング差',
    'tracking_error': 'トラッキングエラー',
    'number_of_holdings': '保有銘柄数',
    'top10_concentration': '上位10銘柄比率',
    'top10_holdings': '上位10銘柄',
    'region_weights': '地域構成',
    'sector_weights': 'セクター構成',
    'overlap_with_current_portfolio': '現在PFとの重複率',
    'overlap_with_sp500': 'S&P500との重複率',
    'overlap_with_fang': 'FANG+との重複率',
    'overlap_with_all_country': 'オルカンとの重複率',
    'cost': 'コスト',
    'tracking_quality': '指数追随性',
    'risk_adjusted_performance': 'リスク調整後実績',
    'downside_risk': '下落リスク',
    'scale_stability': '規模・安定性',
    'diversification': '分散',
    'current_portfolio_fit': 'PF補完性',
    'nisa_growth_eligible': '成長投資枠',
    'nisa_tsumitate_eligible': 'つみたて投資枠',
}

_ROLE_LABELS = {
    'global_core': '全世界株式コア',
    'us_core': '米国株式コア',
    'ex_us': '米国除く世界株式',
    'ex_us_total': '米国除く全世界株式',
    'developed_ex_us': '米国除く先進国株式',
    'Global Core': '全世界株式コア',
    'US Core': '米国株式コア',
    'Ex-US': '米国除く先進国・全世界株式',
    'Small / Value': '小型株・バリュー',
    'Growth': 'グロース',
    'Emerging Markets': '新興国株式',
    'Japan': '日本株式',
    'Gold': '金',
    'Bonds': '債券',
    'Real Assets': '実物資産',
    'Unknown': '未分類',
}

_KNOWN_FUND_DISPLAY = {
    'VT': 'Vanguard Total World Stock ETF (VT)',
    'VTI': 'Vanguard Total Stock Market ETF (VTI)',
    'VXUS': 'Vanguard Total International Stock ETF (VXUS)',
    'VOO': 'Vanguard S&P 500 ETF (VOO)',
}

_PERCENT_METRICS = {
    'expense_ratio', 'total_expense_ratio', 'return_1y', 'return_3y_annualized', 'return_5y_annualized',
    'return_10y_annualized', 'annualized_return', 'volatility', 'max_drawdown', 'tracking_difference',
    'tracking_error', 'top10_concentration', 'overlap_with_current_portfolio', 'overlap_with_sp500',
    'overlap_with_fang', 'overlap_with_all_country',
}


_FUND_METRIC_GROUPS = {
    '基本・コスト・規模': (
        'fund_name', 'ticker', 'asset_class', 'role_category', 'benchmark', 'inception_date', 'currency',
        'expense_ratio', 'total_expense_ratio', 'aum', 'fund_age', 'fund_flow_1m', 'fund_flow_1y',
        'number_of_holdings', 'top10_concentration',
        'us_weight', 'tech_weight', 'small_cap_weight', 'value_exposure', 'growth_exposure',
        'nisa_tsumitate_eligible', 'nisa_growth_eligible', 'sbi_available', 'domestic_alternative',
    ),
    'リターン・リスク': (
        'return_1y', 'return_3y_annualized', 'return_5y_annualized', 'volatility',
        'max_drawdown', 'sharpe_ratio', 'tracking_difference', 'tracking_error',
    ),
    'ポートフォリオ重複': (
        'overlap_with_current_portfolio', 'overlap_with_sp500', 'overlap_with_fang',
        'overlap_with_all_country',
    ),
}

_FUND_SUBJECT_METRICS = {
    metric
    for metrics in _FUND_METRIC_GROUPS.values()
    for metric in metrics
} | {'holdings', 'price_history', 'nav'}


def _metric_display(record):
    if record is None or record.get('status') != 'available':
        return _unavailable_text(record)
    return _format_metric_value(str(record.get('metric') or ''), record.get('value'))


def _metric_label(metric: str) -> str:
    return _METRIC_LABELS.get(metric, metric)


def _format_percent(value: float) -> str:
    return f'{value * 100:.2f}%'


def _format_metric_value(metric: str, value):
    if value is None:
        return '未取得'
    if metric in _PERCENT_METRICS:
        try:
            return _format_percent(float(value))
        except (TypeError, ValueError):
            return '未取得'
    if metric == 'expense_ratio_bps':
        try:
            return f'{float(value):.2f} bps'
        except (TypeError, ValueError):
            return '未取得'
    if metric in {'aum', 'fund_flow_1m', 'fund_flow_1y'}:
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return '未取得'
        if abs(amount) >= 1_000_000_000:
            return f'{amount / 1_000_000_000:.2f}B'
        if abs(amount) >= 1_000_000:
            return f'{amount / 1_000_000:.2f}M'
        return f'{amount:,.0f}'
    if metric == 'number_of_holdings':
        try:
            return f'{int(float(value)):,}'
        except (TypeError, ValueError):
            return '未取得'
    if metric == 'top10_holdings' and isinstance(value, list):
        rows = []
        for item in value[:10]:
            if not isinstance(item, dict):
                continue
            name = str(item.get('security_name') or item.get('security_id') or item.get('ticker') or 'unknown')
            weight = item.get('weight')
            try:
                rows.append(name + ' ' + _format_percent(float(weight)))
            except (TypeError, ValueError):
                rows.append(name)
        return ' / '.join(rows) if rows else '未取得'
    if isinstance(value, bool):
        return 'はい' if value else 'いいえ'
    if isinstance(value, (int, float)):
        return f'{float(value):.4f}'
    if isinstance(value, dict):
        pairs = []
        for key, item_value in value.items():
            try:
                pairs.append(f'{key}: {_format_percent(float(item_value))}')
            except (TypeError, ValueError):
                pairs.append(f'{key}: {item_value}')
        return ', '.join(pairs)
    if isinstance(value, list):
        return f'系列 {len(value)}点'
    return str(value)


def _unavailable_text(record):
    if record is None:
        return '未取得'
    reason = str(record.get('reason') or '')
    if reason == 'dependency_failed':
        dependency = str(record.get('dependency') or '依存データ')
        return f'計算不可（理由: {dependency}未取得）'
    if reason in {'provider_disabled', 'resource_not_configured', 'license_not_approved', 'api_key_unavailable', 'holdings_not_available_from_source'}:
        return '取得不可'
    if reason in {'provider_returned_no_observations', 'benchmark_series_unavailable', 'risk_free_series_unavailable'}:
        return '取得不可'
    return '未取得'


def _table(headers, rows, klass=''):
    class_attr = f' class="{escape(klass, quote=True)}"' if klass else ''
    head = '<div class="scroll"><table' + class_attr + '><thead><tr>' + ''.join('<th>' + escape(str(header)) + '</th>' for header in headers) + '</tr></thead><tbody>'
    def render_cell(cell):
        if isinstance(cell, tuple) and len(cell) == 2 and cell[0] == 'html':
            return '<td>' + cell[1] + '</td>'
        return '<td>' + escape(str(cell)) + '</td>'
    body = ''.join('<tr>' + ''.join(render_cell(cell) for cell in row) + '</tr>' for row in rows)
    return head + body + '</tbody></table></div>'


def _best_records_by_subject_metric(records, subjects):
    by_subject_metric = {}
    for record in records:
        subject = record.get('subject')
        if subject not in subjects:
            continue
        key = (subject, record.get('metric'))
        current = by_subject_metric.get(key)
        rank = (
            record.get('status') == 'available',
            record.get('freshness_status') == 'fresh',
            record.get('source_role') == 'primary',
            record.get('fetched_at') or '',
        )
        if current is None or rank > current[0]:
            by_subject_metric[key] = (rank, record)
    return {key: value[1] for key, value in by_subject_metric.items()}


def _role_label(raw_role):
    text = str(raw_role or 'Unknown').strip()
    return _ROLE_LABELS.get(text, text)


def _subject_display_name(subject, by_subject_metric):
    fund_name_record = by_subject_metric.get((subject, 'fund_name'))
    ticker_record = by_subject_metric.get((subject, 'ticker'))
    if fund_name_record and fund_name_record.get('status') == 'available':
        name = str(fund_name_record.get('value'))
        ticker = str(ticker_record.get('value')) if ticker_record and ticker_record.get('status') == 'available' else subject
        return f'{name} ({ticker})'
    return _KNOWN_FUND_DISPLAY.get(subject, subject)


def _subject_role(subject, by_subject_metric):
    role_record = by_subject_metric.get((subject, 'role_category'))
    if role_record and role_record.get('status') == 'available':
        return _role_label(role_record.get('value'))
    if subject == 'VT':
        return _ROLE_LABELS['Global Core']
    return _ROLE_LABELS['Unknown']


def _fund_anchor(subject, by_subject_metric):
    ticker = by_subject_metric.get((subject, 'ticker'), {}).get('value') or subject
    slug = re.sub(r'[^A-Za-z0-9]+', '-', str(ticker).lower()).strip('-') or 'fund'
    return 'fund-detail-' + slug


def _availability_score(subject, by_subject_metric):
    core = ('expense_ratio', 'aum', 'return_5y_annualized', 'max_drawdown', 'top10_concentration', 'overlap_with_current_portfolio')
    available = sum(
        1
        for metric in core
        if by_subject_metric.get((subject, metric), {}).get('status') == 'available'
    )
    return available, len(core)


def _render_api_fund_metrics(info):
    records = info.get('external_data', {}).get('records', [])
    fund_subjects = sorted({
        record.get('subject')
        for record in records
        if record.get('metric') in _FUND_SUBJECT_METRICS
        and record.get('subject') not in (None, '', 'unconfigured')
        and record.get('provider_id') != 'analysis_engine'
    })
    if not fund_subjects:
        return '<h3>候補ファンド比較</h3><p>対象ファンドの取得済み評価データがありません。取得条件を満たしたデータが揃うまで比較表を表示しません。</p>'

    by_subject_metric = _best_records_by_subject_metric(records, fund_subjects)
    evaluations = {
        item.get('fund_id'): item
        for item in info.get('external_data', {}).get('fund_evaluations', [])
        if item.get('fund_id')
    }
    by_role = {}
    for subject in fund_subjects:
        role = _subject_role(subject, by_subject_metric)
        by_role.setdefault(role, []).append(subject)

    body = '<h3>候補ファンド比較</h3><p>比較表は意思決定用の主要指標だけを表示します。取得元・スコア内訳・各metricの詳細は <a href="#data-methodology">Data &amp; Methodology</a> を参照してください。</p>'
    table_headers = ['評価状態', '候補数', 'ファンド', 'ティッカー', '経費率', '純資産総額', '5年年率リターン', '最大下落率', 'シャープレシオ', '総合スコア', 'データ充足率', '詳細']
    role_order = [
        _ROLE_LABELS['Global Core'], _ROLE_LABELS['US Core'], _ROLE_LABELS['ex_us'], _ROLE_LABELS['ex_us_total'], _ROLE_LABELS['developed_ex_us'], _ROLE_LABELS['Ex-US'], _ROLE_LABELS['Small / Value'],
        _ROLE_LABELS['Growth'], _ROLE_LABELS['Emerging Markets'], _ROLE_LABELS['Japan'], _ROLE_LABELS['Gold'],
        _ROLE_LABELS['Bonds'], _ROLE_LABELS['Real Assets'], _ROLE_LABELS['Unknown'],
    ]
    for role in role_order:
        subjects = by_role.get(role, [])
        if not subjects:
            continue
        ranked = sorted(
            subjects,
            key=lambda subject: (evaluations.get(subject, {}).get('score') is not None, evaluations.get(subject, {}).get('score') or -1),
            reverse=True,
        )[:5]
        rows = []
        for subject in ranked:
            evaluation = evaluations.get(subject, {})
            ticker_record = by_subject_metric.get((subject, 'ticker'))
            evaluation_status = evaluation.get('evaluation_status', '参考スコア').split('（', 1)[0]
            rows.append([
                evaluation_status,
                f"{evaluation.get('candidate_count', len(subjects))}/5",
                _subject_display_name(subject, by_subject_metric),
                _metric_display(ticker_record),
                _metric_display(by_subject_metric.get((subject, 'expense_ratio'))),
                _metric_display(by_subject_metric.get((subject, 'aum'))),
                _metric_display(by_subject_metric.get((subject, 'return_5y_annualized'))),
                _metric_display(by_subject_metric.get((subject, 'max_drawdown'))),
                _metric_display(by_subject_metric.get((subject, 'sharpe_ratio'))),
                _score_display(evaluation.get('score')),
                _coverage_display(evaluation.get('data_coverage_score')),
                ('html', '<a href="#' + escape(_fund_anchor(subject, by_subject_metric), quote=True) + '">詳細</a>'),
            ])
        body += '<h4>' + escape(role) + ' 上位' + str(len(rows)) + 'ファンド</h4>'
        body += _table(table_headers, rows, klass='role-top5')

    return body


def _score_display(value):
    return '計算不可' if value is None else f'{float(value):.0f} / 100'


def _coverage_display(value):
    return '0%' if value is None else f'{float(value):.0f}%'


def _render_score_details(evaluation):
    if not evaluation:
        return ''
    rows = []
    for category, item in evaluation.get('categories', {}).items():
        label = _metric_label(category)
        score = _score_display(item.get('score'))
        available = float(item.get('available_weight', 0))
        weight = float(item.get('weight', 0))
        rows.append([label, score, f'{available:.1f} / {weight:.1f}'])
    total_available = sum(float(item.get('available_weight', 0)) for item in evaluation.get('categories', {}).values())
    total_weight = sum(float(item.get('weight', 0)) for item in evaluation.get('categories', {}).values())
    body = '<h5>Coverage Breakdown</h5>' + _table(['評価軸', '利用可能weight / total weight', '点数'], [[row[0], row[2], row[1]] for row in rows])
    body += '<p class="muted">Total Coverage: ' + f'{total_available / total_weight * 100:.0f}%' + '</p>' if total_weight else ''
    return body + '<h5>スコア内訳</h5>' + _table(['評価軸', '点数', '配点'], [[row[0], row[1], 'weight ' + row[2].split(' / ')[1] + '%'] for row in rows])


def _render_fund_details(subject, by_subject_metric, evaluation=None):
    name = _subject_display_name(subject, by_subject_metric)
    basic_metrics = ('fund_name', 'ticker', 'asset_class', 'role_category', 'benchmark')
    cost_metrics = ('expense_ratio', 'expense_ratio_bps', 'total_expense_ratio', 'aum', 'fund_age', 'fund_flow_1m', 'fund_flow_1y')
    risk_metrics = ('return_1y', 'return_3y_annualized', 'return_5y_annualized', 'annualized_return', 'volatility', 'max_drawdown', 'sharpe_ratio', 'tracking_difference', 'tracking_error')
    holdings_metrics = ('number_of_holdings', 'top10_concentration', 'top10_holdings')
    exposure_metrics = ('region_weights', 'sector_weights')
    overlap_metrics = ('overlap_with_current_portfolio', 'overlap_with_sp500', 'overlap_with_fang', 'overlap_with_all_country')

    body = '<details id="' + escape(_fund_anchor(subject, by_subject_metric), quote=True) + '"><summary>' + escape(name) + ' の詳細</summary>'
    body += _render_score_details(evaluation)
    body += _detail_table('基本情報', subject, basic_metrics, by_subject_metric)
    body += _detail_table('コスト', subject, cost_metrics, by_subject_metric)
    body += _detail_table('リターン・リスク', subject, risk_metrics, by_subject_metric)
    body += _detail_table('Holdings', subject, holdings_metrics, by_subject_metric)
    body += _detail_table('地域・セクター', subject, exposure_metrics, by_subject_metric)
    body += _detail_table('PF重複', subject, overlap_metrics, by_subject_metric)

    sample = next(
        (
            by_subject_metric.get((subject, metric))
            for metric in ('expense_ratio', 'price_history', 'benchmark_price_history', 'benchmark', 'holdings')
            if by_subject_metric.get((subject, metric)) is not None
        ),
        next((record for (record_subject, _), record in by_subject_metric.items() if record_subject == subject), None),
    )
    if sample is not None:
        source_rows = [
            ['provider', sample.get('provider_name') or sample.get('source_name') or '未取得'],
            ['source', sample.get('source_name') or '未取得'],
            ['source_url', sample.get('source_url') or '未取得'],
            ['as_of', sample.get('as_of') or '未取得'],
            ['fetched_at', sample.get('fetched_at') or '未取得'],
            ['expires_at', sample.get('expires_at') or '未取得'],
            ['freshness', sample.get('freshness_status') or '未取得'],
            ['confidence', sample.get('confidence') or '未取得'],
            ['license', sample.get('license_status') or '未取得'],
            ['report_period', sample.get('report_period') or '未取得'],
            ['dependency', sample.get('dependency') or ''],
            ['root_cause', sample.get('root_cause') or sample.get('reason') or ''],
        ]
        body += _table(['Data source', '値'], source_rows)
    body += '</details>'
    return body


def _detail_table(title, subject, metrics, by_subject_metric):
    rows = []
    for metric in metrics:
        record = by_subject_metric.get((subject, metric))
        rows.append([_metric_label(metric), _metric_display(record)])
    return '<h5>' + escape(title) + '</h5>' + _table(['項目', '値'], rows)


def render_portfolio_options(info):
    body = '<section id="portfolio-options"><h2>推奨ポートフォリオ設計</h2><p>まず役割カテゴリごとの配分を示し、その後に各役割の実装候補を比較します。特定商品の自動推奨は行いません。</p>'
    allocation_rows = [[item['role'], f"{item['weight_pct']}%", item['purpose']] for item in _PORTFOLIO_ALLOCATION]
    allocation_rows.append(['合計', f"{sum(item['weight_pct'] for item in _PORTFOLIO_ALLOCATION)}%", ''])
    body += '<h3>役割カテゴリ配分</h3>'
    body += simple_table(['役割・ジャンル', '目標配分', '設計意図'], allocation_rows)
    body += '<p>各roleの実装候補は次の比較表で確認します。</p>'
    body += _render_api_fund_metrics(info)
    body += '<p class="muted">各ジャンルの実装候補は、上のAPI metricデータに加えてSBI取扱可否、NISA対象、データ鮮度を確認した後に最大5件まで絞ります。現時点では自動順位付け・選定は行いません。</p></section>'
    return body


def render_contribution_plans(info):
    goal = {**info['profile'], **info['goals']}
    steps = goal['contributions_yen']
    amounts = [steps['through_age_25'], steps['age_26_to_29'], steps['age_30_plus_tentative']]
    headings = ['23～25歳', '26歳以降', '30歳以降（25万円・検討中）']
    body = '<section id="contributions"><h2>積立プラン：ジャンル配分を毎月額へ換算</h2><p>合計月額は現在15万円、26歳以降20万円、30歳から25万円は検討ケースです。</p>'
    rows = [[item['role'], f"{item['weight_pct']}%", *(yen(amount * item['weight_pct'] // 100) + '円' for amount in amounts)] for item in _PORTFOLIO_ALLOCATION]
    rows.append(['合計', '100%', *(yen(amount) + '円' for amount in amounts)])
    body += simple_table(['役割・ジャンル', '比率'] + headings, rows)
    body += '<p>NISA対象なら、つみたて投資枠を優先し、成長投資枠は対象商品・年間枠・残枠を確認して使います。NISA利用可能額を超える積立は特定口座へ回す想定です。具体的な枠振分は採用案と商品対象の確認後に決めます。</p></section>'
    return body


def render_existing_assets(info):
    snapshot = info['snapshot']
    groups = {row['name']: row for row in snapshot['categories']}
    accounts = {row['name']: row for row in snapshot['accounts']}
    junior = accounts.get('ジュニアNISA-旧NISA預り', {}).get('value_yen', 0)
    rows = [
        ['S&P500系', yen(groups['S&P500系']['value_yen']) + '円', '既存分の保有判断と今後の買付比率は別に決める。'],
        ['FANG+', yen(groups['FANG+']['value_yen']) + '円（' + pct(groups['FANG+']['weight_pct']) + '）', '新規積立の停止は、既存分を全売却することを意味しない。既存分の扱いは別途判断。'],
        ['8資産均等型', yen(groups['8資産均等型']['value_yen']) + '円', '既存保有を前提に、新規積立でさらに増やすか検討。内部配分は基準日付き資料が必要。'],
        ['ジュニアNISA-旧NISA預り', yen(junior) + '円', '名義・目的の確認前に本人の目標資産と同一視しない。'],
        ['旧NISA預り', yen(accounts.get('旧NISA預り', {}).get('value_yen', 0)) + '円', '旧制度資産。新NISA生涯枠の残り額には加減しない。'],
    ]
    body = '<section id="existing-assets"><h2>既存資産の扱い</h2><p>今後何を買うかと、既に保有するものをどう扱うかは別の判断です。</p>'
    body += simple_table(['保有区分', '現在評価額', '検討時の切り分け'], rows)
    body += '<p class="notice">このレポートは積立先の比較であり、保有商品の売買を実行・指示しません。口座名義、ジュニアNISAの目的、生活防衛資金を確認してから既存資産の扱いを決めます。</p></section>'
    return body


def render_risk_scenarios(info):
    snapshot, diagnosis = info['snapshot'], info['diagnosis']
    stock = int(Decimal(next(row for row in diagnosis['asset_estimate'] if row['name'] == '株式（概算）')['value_yen']))
    total = snapshot['totals']['value_yen']
    rows = []
    for shock in (-20, -30, -40):
        loss = int((Decimal(stock) * Decimal(shock) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        rows.append([f'{shock}%', yen(loss, True) + '円', yen(total + loss) + '円'])
    body = '<section id="risk"><h2>リスク・ストレステスト</h2><p>株式概算部分のみを一律に下落させ、債券・REIT・他条件は不変とした機械的シナリオです。発生確率や最大損失を示すものではありません。</p>'
    body += simple_table(['株式価格の仮定', '投信評価額への影響', '残る投信評価額'], rows)
    body += '<h3>今回の入力では定量化できないリスク</h3><ul><li>円高：ファンド別の通貨・為替ヘッジ比率がないため影響額は算出しません。</li><li>米国大型テック下落：同一基準日の組入銘柄と重複率がないため、S&amp;P500・オルカン・FANG+を重複計上せずに試算できません。</li><li>Gold：今回のジャンル配分案では10%を設定していますが、Goldは現保有に含まれません。提案比率を含む将来資産全体のGoldストレスは未算出です。</li></ul></section>'
    return body


def _fund_status(records, subject):
    metrics = {record.get('metric'): record for record in records if record.get('subject') == subject}
    tracked = ('expense_ratio', 'aum', 'return_5y_annualized', 'volatility', 'max_drawdown', 'sharpe_ratio', 'tracking_error', 'top10_concentration', 'overlap_with_current_portfolio')
    statuses = [metrics[metric].get('status') for metric in tracked if metric in metrics]
    available = sum(status == 'available' for status in statuses)
    if not statuses:
        return '未取得'
    if available == len(statuses):
        return '全取得'
    if available == 0:
        return '取得不可'
    return '一部未取得'


def _render_fund_data_status(records, evaluations=None):
    evaluations = evaluations or {}
    subjects = sorted({
        record.get('subject')
        for record in records
        if record.get('metric') == 'fund_name'
        and record.get('subject') not in (None, '', 'unconfigured')
    })
    by_subject_metric = _best_records_by_subject_metric(records, subjects)
    rows = []
    for subject in subjects:
        role = _subject_role(subject, by_subject_metric)
        fetched_at = max(
            (str(record.get('fetched_at') or '') for (record_subject, _), record in by_subject_metric.items() if record_subject == subject),
            default='',
        )
        evaluation = evaluations.get(subject, {})
        rows.append([
            _subject_display_name(subject, by_subject_metric),
            _metric_display(by_subject_metric.get((subject, 'ticker'))),
            role,
            _coverage_display(evaluation.get('data_coverage_score')),
            _fund_status(records, subject),
            fetched_at[:10] if fetched_at else '未取得',
            ('html', '<a href="#' + escape(_fund_anchor(subject, by_subject_metric), quote=True) + '">詳細</a>'),
        ])
    return _table(
        ['ファンド', 'ティッカー', 'Role', 'Coverage', '取得状態', '最終更新', '詳細'],
        rows,
        klass='fund-data-status',
    )


def _render_provider_coverage(records, subjects):
    by_subject_metric = _best_records_by_subject_metric(records, subjects)
    provider_subjects = {}
    for subject in subjects:
        metadata = by_subject_metric.get((subject, 'fund_name'), {})
        provider = str(metadata.get('source_name') or metadata.get('provider_name') or 'unknown')
        provider_subjects.setdefault(provider, []).append(subject)
    metrics = ('expense_ratio', 'aum', 'price_history', 'benchmark', 'holdings')
    rows = []
    for provider, provider_funds in sorted(provider_subjects.items()):
        cells = []
        for metric in metrics:
            available = sum(
                by_subject_metric.get((subject, metric), {}).get('status') == 'available'
                for subject in provider_funds
            )
            cells.append(f'{available}/{len(provider_funds)}')
        rows.append([provider, len(provider_funds), *cells])
    return _table(['Provider', 'Funds', 'Expense', 'AUM', 'Price', 'Benchmark', 'Holdings'], rows, klass='provider-coverage')


def render_data_methodology(info):
    from history_report import render_history

    snapshot, assumptions = info['snapshot'], info['assumptions']
    diagnosis = info['diagnosis']
    nisa = assumptions['nisa_snapshot']
    ref = assumptions['handover_reference']
    gain_gap = snapshot['totals']['gain_yen'] - ref['fund_gain_yen']
    body = '<section id="data-methodology"><h2>Data &amp; Methodology</h2>'
    body += simple_table(['入力・方法', '出典日/前提', '扱い'], [
        ['保有CSV', snapshot['export_timestamp_inferred'][:10] + '（出力日時推定 ' + snapshot['export_timestamp_inferred'][11:] + '）', '価格評価基準日時は不明'],
        ['注文履歴', info['history']['orders']['metadata']['発注開始年月日'] + ' ～ ' + info['history']['orders']['metadata']['発注終了年月日'], '発注記録'],
        ['約定履歴', info['history']['trades']['metadata']['約定開始年月日'] + ' ～ ' + info['history']['trades']['metadata']['約定終了年月日'], '受渡金額の買付実績'],
        ['NISA画面転記', nisa['source_date'], '画面の最大利用可能額を正とし、CSV等から再推計しない'],
    ])
    body += '<h3>計算と限界</h3><ul><li>取得金額と評価損益は保有明細のみを合算。取得金額は累計入金額ではありません。</li><li>目標試算は月末拠出、一定年率、実効年率を月率へ換算。税・費用・分配・為替を考慮しません。</li><li>資産クラス概算は8資産均等型の基本比率を適用し、その他ファンドを株式として計算。</li><li>NISA利用状況は画面転記、NISA口座の現在残高は保有CSV。現在残高を年間・生涯枠利用額とみなしません。</li><li>現保有ファンドの費用率、正確な地域・銘柄重複、NISA残枠の将来変化は未確認です。</li></ul>'
    external = info.get('external_data', {})
    external_records = external.get('records', [])
    api_fund_subjects = {
        record.get('subject')
        for record in external_records
        if record.get('provider_id') != 'analysis_engine'
        and record.get('metric') in _FUND_SUBJECT_METRICS
        and record.get('subject') not in (None, '', 'unconfigured')
    }
    by_subject_metric = _best_records_by_subject_metric(external_records, api_fund_subjects)
    evaluations = {
        item.get('fund_id'): item
        for item in external.get('fund_evaluations', [])
        if item.get('fund_id')
    }
    body += '<h3>Scoring Methodology</h3><p>総合スコア = 利用可能metricの加重点 / 利用可能weight × 100。Data Coverage = 利用可能weight / total weight × 100。metricはRole内percentileで正規化し、missingは0点ではなくweightを除外して再正規化します。Fund QualityとPortfolio Fitを分離し、coverage 60%未満は順位対象外です。</p>'
    body += '<h3>Provider Coverage</h3>' + _render_provider_coverage(external_records, api_fund_subjects)
    body += '<h3>候補ファンド データ取得状況</h3><p>通常表示は1行1ファンドです。metric単位の状態・依存関係・出典日時は下のFund Data Detailsにまとめています。</p>'
    if api_fund_subjects:
        body += _render_fund_data_status(external_records, evaluations)
        body += '<h3>Fund Data Details</h3><p>候補ファンド比較の「詳細」リンクから、この一覧の該当ファンドへ移動できます。</p>'
        for subject in sorted(api_fund_subjects):
            body += _render_fund_details(subject, by_subject_metric, evaluations.get(subject))
    else:
        body += '<p>外部providerのデータ記録はありません。</p>'
    body += '<p class="muted">availability: ' + str(external.get('available_count', 0)) + ' available / ' + str(external.get('unavailable_count', 0)) + ' unavailable. 公開許可済みの月次snapshotは `data/market/` に保存されGit管理対象外です。raw responseは明示許可がない限り保持しません。</p>'
    body += '<h3>公式資料</h3><ul>'
    for item in diagnosis['sources']:
        body += '<li><a href="' + escape(item['url'], quote=True) + '">' + escape(item['title']) + '</a>（確認日 ' + escape(item['accessed']) + '）</li>'
    body += '</ul>'
    body += '<div class="notice">引継ぎ文書の損益 ' + yen(ref['fund_gain_yen'], True) + '円と今回CSVの ' + yen(snapshot['totals']['gain_yen'], True) + '円との差は ' + yen(gain_gap, True) + '円で、原因未確認です。引継ぎ参考の銀行残高 ' + yen(ref['bank_balance_yen']) + '円は今回CSVの投信評価額に加算していません。</div>'
    body += '<details><summary>保有明細・注文と約定の照合データ</summary>'
    body += simple_table(['口座', 'ファンド', '評価額', '取得金額', '損益', '口数'], [[row['account'], row['fund'], yen(row['value_yen']) + '円', yen(row['cost_yen']) + '円', yen(row['gain_yen'], True) + '円', yen(row['units'])] for row in snapshot['holdings']])
    body += render_history(info)
    body += '<p>原本SHA-256: <span class="mono">' + escape(snapshot['source_sha256']) + '</span></p></details></section>'
    return body


def render_dashboard_content(info, tag):
    snapshot, assumptions = info['snapshot'], info['assumptions']
    profile, goal = info['profile'], info['goals']
    nisa = assumptions['nisa_snapshot']
    total = snapshot['totals']['value_yen']
    target_date = date(profile['birth_year'] + goal['target_age'], profile['birth_month'], 1)
    current_date = date.fromisoformat(snapshot['export_timestamp_inferred'][:10])
    current_age = current_date.year - profile['birth_year'] - (current_date.month < profile['birth_month'])
    remaining_months = (target_date.year - current_date.year) * 12 + target_date.month - current_date.month
    current_nisa = nisa['monthly_contributions_yen']['current']
    current_monthly = sum(current_nisa.values())
    progress = total / goal['target_yen'] * 100
    cards = [
        _card('現在の投資信託評価額', yen(total), '円'),
        _card('35歳目標', _short_yen(goal['target_yen'])),
        _card('目標への進捗', f'{progress:.1f}', '%'),
        _card('現在の積立設定', yen(current_monthly), '円 / 月'),
        _card('26歳以降の想定', yen(sum(nisa['monthly_contributions_yen']['age_26_plus'].values())), '円 / 月'),
        _card('NISA生涯利用額', yen(nisa['lifetime']['total']['used_yen']) + ' / ' + yen(nisa['lifetime']['total']['limit_yen']), '円'),
    ]
    body = header(snapshot, 'Asset Management Dashboard')
    body += '<div class="cards">' + ''.join(cards) + '</div>'
    body += '<section><h2>35歳までに1億円</h2><p>' + f'{profile["birth_year"]}/{profile["birth_month"]:02d}' + ' 生まれ　→　現在 ' + escape(current_date.strftime('%Y/%m')) + f'・{current_age}歳' + '　→　目標 ' + escape(target_date.strftime('%Y/%m')) + '</p>'
    body += '<p><strong>現在 ' + yen(total) + '円から、残り約' + f'{remaining_months // 12}年{remaining_months % 12}か月' + 'で ' + escape(_short_yen(goal['target_yen'])) + 'を目指す計画です。</strong>銀行残高を含まず、ジュニアNISAを含む投資信託評価額です。</p>'
    life = nisa['lifetime']['total']
    body += '<p>NISA：使用 ' + yen(life['used_yen']) + '円 / 1,800万円、生涯残り ' + yen(life['remaining_yen']) + '円（' + escape(nisa['source_date']) + '画面転記）。</p></section>'
    body += '<section><h2>Latest Report</h2><p><strong>' + tag[:4] + '年' + str(int(tag[4:6])) + '月 資産分析</strong></p><p><a class="button-link" href="reports/monthly/資産分析_' + tag[:6] + '.html">レポートを見る</a> <a href="reports/index.html">月次レポート一覧</a></p></section>'
    rows = []
    for rate in goal['annual_return_scenarios_pct']:
        base = goal_projection(total, snapshot['export_timestamp_inferred'], rate, False, {**profile, **goal})['value_yen']
        raised = goal_projection(total, snapshot['export_timestamp_inferred'], rate, True, {**profile, **goal})['value_yen']
        rows.append([f'{rate}%', yen(base) + '円', yen(raised) + '円'])
    body += '<section><h2>Goal Projection</h2><p class="muted">一定年率で積み立てた場合の機械的シナリオ。予測・達成保証ではありません。</p>'
    body += simple_table(['想定年率', '月20万円継続', '30歳から月25万円（検討中）'], rows) + '</section>'
    body += '<section><h2>Portfolio</h2>' + bars(snapshot['categories']) + '</section>'
    body += '<footer><a href="README.md">Developer Documentation</a> / <a href="reports/index.html">月次レポート一覧</a></footer>'
    return body