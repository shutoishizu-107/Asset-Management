"""Sections for the goal-first monthly investment report."""
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from datetime import date
import json

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
        ['集中リスク', 'S&amp;P500系 ' + pct(groups['S&P500系']['weight_pct']) + '、FANG+ ' + pct(groups['FANG+']['weight_pct']) + '。', 'オルカン内の米国大型株も重なる可能性があるが、組入明細がないため重複率・実質米国比率は算出不可。'],
        ['分散', '8資産均等型 ' + pct(groups['8資産均等型']['weight_pct']) + '。概算株式 ' + pct(stock['weight_pct']) + '、債券 ' + pct(bonds['weight_pct']) + '、REIT ' + pct(reits['weight_pct']) + '。', '現金と同じ安全資産ではない。基礎配分によるモデル推計。'],
        ['コスト', '現在保有ファンドの信託報酬・総経費率は保有CSVに含まれない。', '確認済みの現行費用比較がないため、候補表の概算値を保有中ファンドへ流用しない。'],
        ['積立方針', 'S&amp;P500系・オール・カントリー・FANG+に各月' + yen(monthly['S&P500系']) + '円。', '新規積立の3分の1がFANG+に向かう設定。既存残高の比率とは分けて見直す。'],
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


def _linked_table(headers, rows):
    head = '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + escape(str(value)) + '</th>' for value in headers) + '</tr></thead><tbody>'
    body = []
    for row in rows:
        cells = []
        for value in row:
            if isinstance(value, tuple):
                label, url = value
                cells.append('<td><a href="' + escape(url, quote=True) + '">' + escape(label) + '</a></td>')
            else:
                cells.append('<td>' + escape(str(value)) + '</td>')
        body.append('<tr>' + ''.join(cells) + '</tr>')
    return head + ''.join(body) + '</tbody></table></div>'


def render_fund_comparison(info):
    candidate_data = info['fund_candidates']
    by_name = {row['fund']: row for row in candidate_data['items']}
    eligibility = candidate_data['nisa_eligibility']
    shortlist_rows = []
    for fund in candidate_data['priority_funds']:
        item = by_name[fund]
        nisa = eligibility.get(fund, {})
        shortlist_rows.append([
            fund,
            item['main_role'],
            item['cost_estimate'],
            nisa.get('tsumitate', '未確認'),
            nisa.get('growth', '未確認'),
            nisa.get('role', '枠対象確認後に決定'),
            nisa.get('taxable', '枠満額後の候補'),
            ('公式資料', nisa['source_url']) if nisa.get('source_url') else '未確認',
        ])
    body = '<section id="funds"><h2>投資候補ファンド比較</h2><h3>役割別ショートリスト</h3>'
    body += '<p>費用と既存PFとの重複はユーザー提供の概算入力です。NISA対象は公式ページで対象表示を確認できた項目だけ記載し、「表示なし」は対象外を意味しません。</p>'
    body += _linked_table(['候補', '役割', 'コスト目安（入力）', 'つみたて投資枠', '成長投資枠', 'NISA内の役割案', '特定口座の扱い案', '確認先'], shortlist_rows)
    body += '<p class="muted">オルカンとS&amp;P500は両枠対象、ニッセイNASDAQ100とTracers S&amp;P1000は成長投資枠対象を各運用会社の商品ページで確認。全世界（除く米国）とGold Hなしはこの確認では枠対象を確定できません。販売会社の取扱・積立設定も購入前に確認してください。</p>'
    evaluations = {item['fund_id']: item for item in info.get('external_data', {}).get('fund_evaluations', [])}
    readiness_rows = []
    for fund in candidate_data['priority_funds']:
        status = evaluations.get(fund, {'status': 'unavailable', 'missing_or_stale_metrics': ['expense_ratio', 'aum', 'holdings', 'benchmark']})
        readiness_rows.append([fund, status['status'], ', '.join(status['missing_or_stale_metrics']) or 'なし', '算出しない'])
    body += '<h3>データ充足度（自動推奨ではなく確認状況）</h3>'
    body += simple_table(['候補ファンド', 'データ状態', '不足/古い指標', '評価点・推奨'], readiness_rows)
    body += '<p class="muted">必要データが未取得またはstaleなら、スコアや優劣の推奨は作りません。確認できるファンドだけの平均・合成値も生成しません。</p>'
    body += '<h3>ファンド・ユニバース</h3><p>21件の入力一覧は詳細比較が必要な場合にのみ開けます。重複・分散の評価は未検証メモです。</p><details><summary>候補21本と全評価軸を表示</summary>'
    rows = []
    for item in candidate_data['items']:
        nisa = eligibility.get(item['fund'], {})
        rows.append([
            item['fund'], item['investment_target'], item['cost_estimate'], item['existing_overlap'], item['diversification'], item['main_role'], item['consideration'],
            nisa.get('tsumitate', '未確認'), nisa.get('growth', '未確認'), nisa.get('role', '対象枠を確認後に検討'), nisa.get('taxable', '特定口座での保有可否を要確認'),
        ])
    body += simple_table(['ファンド', '投資対象', 'コスト目安（入力）', '既存PFとの重複', '分散効果', '主な役割', '検討位置', 'つみたて枠', '成長枠', 'NISAでの役割', '特定口座'], rows)
    body += '<p class="muted">' + escape(candidate_data['status_note']) + '</p>'
    for note in candidate_data.get('verification_notes', []):
        body += '<p class="notice">' + escape(note['verified_note']) + ' <a href="' + escape(note['source_url'], quote=True) + '">公式資料</a></p>'
    body += '</details></section>'
    return body


_PORTFOLIO_PLANS = [
    {'name':'参考案：All Country 60 / S&P500 30 / FANG+ 10', 'purpose':'既存候補の比較基準', 'stock':'100%（仮定）', 'us':'高め。実質比率は未確認', 'concentration':'高め（FANG+を含む）', 'diversification':'世界株＋米国大型株。ただし重複あり', 'allocations':[('eMAXIS Slim オルカン',60),('eMAXIS Slim S&P500',30),('iFreeNEXT FANG+',10)]},
    {'name':'案A：成長型', 'purpose':'NASDAQ100と米国中小型を上乗せ', 'stock':'100%（仮定）', 'us':'高め。実質比率は未確認', 'concentration':'高め（大型成長を追加）', 'diversification':'米国の企業規模を追加', 'allocations':[('eMAXIS Slim オルカン',60),('eMAXIS Slim S&P500',20),('ニッセイNASDAQ100',10),('Tracers S&P1000',10)]},
    {'name':'案B：地域・サイズ分散型', 'purpose':'NASDAQ100を使わず非米国を追加', 'stock':'100%（仮定）', 'us':'分散を追加。実質比率は未確認', 'concentration':'成長集中を抑える設計', 'diversification':'S&P1000＋全世界（除く米国）', 'allocations':[('eMAXIS Slim オルカン',60),('eMAXIS Slim S&P500',20),('Tracers S&P1000',10),('SBI・V・全世界（除く米国）',10)]},
    {'name':'案C：Gold 10%型', 'purpose':'株式中心にGoldを10%組み合わせる', 'stock':'90%（Goldを株式外と仮定）', 'us':'高め。実質比率は未確認', 'concentration':'成長サテライトあり', 'diversification':'Gold 10%を追加', 'allocations':[('eMAXIS Slim オルカン',60),('eMAXIS Slim S&P500',20),('ニッセイNASDAQ100',10),('SBI-iShares Gold Hなし',10)]},
    {'name':'案D：Gold 5%型', 'purpose':'株式中心にGoldを5%組み合わせる', 'stock':'95%（Goldを株式外と仮定）', 'us':'高め。実質比率は未確認', 'concentration':'成長サテライトあり', 'diversification':'Gold 5%を追加', 'allocations':[('eMAXIS Slim オルカン',65),('eMAXIS Slim S&P500',20),('ニッセイNASDAQ100',10),('SBI-iShares Gold Hなし',5)]},
]


def render_portfolio_options():
    body = '<section id="portfolio-options"><h2>ポートフォリオ候補</h2><p>すべて新規積立額に対する未採用シナリオです。株式比率はGoldを株式外とした機械的区分。米国比率・費用加重平均・銘柄重複率は未算出です。</p>'
    rows = [[plan['name'], plan['purpose'], plan['stock'], plan['us'], plan['concentration'], plan['diversification'], '未算出（費用入力未検証）'] for plan in _PORTFOLIO_PLANS]
    body += simple_table(['案', '目的', '株式比率', '米国集中', '株式集中', '分散の役割', '加重コスト'], rows)
    body += '<p class="muted">候補を順位付けする表ではありません。本人の継続可能性と値下がり許容度を確認して比較します。</p></section>'
    return body


def render_contribution_plans(info):
    goal = {**info['profile'], **info['goals']}
    steps = goal['contributions_yen']
    amounts = [steps['through_age_25'], steps['age_26_to_29'], steps['age_30_plus_tentative']]
    headings = ['23～25歳', '26歳以降', '30歳以降（25万円・検討中）']
    body = '<section id="contributions"><h2>積立プラン：比率を毎月の金額へ変換</h2><p>合計月額は現在15万円、26歳以降20万円、30歳から25万円は検討ケースです。</p>'
    for plan in _PORTFOLIO_PLANS:
        body += '<h3>' + escape(plan['name']) + '</h3>'
        rows = [[fund, f'{weight}%', yen(amounts[0] * weight // 100) + '円', yen(amounts[1] * weight // 100) + '円', yen(amounts[2] * weight // 100) + '円'] for fund, weight in plan['allocations']]
        rows.append(['合計', '100%', yen(amounts[0]) + '円', yen(amounts[1]) + '円', yen(amounts[2]) + '円'])
        body += simple_table(['ファンド', '比率'] + headings, rows)
    body += '<p>NISA対象なら、つみたて投資枠を優先し、成長投資枠は対象商品・年間枠・残枠を確認して使います。NISA利用可能額を超える積立は特定口座へ回す想定です。各案の具体的な枠振分は採用案と商品対象の確認後に決めます。</p></section>'
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
    body += '<h3>今回の入力では定量化できないリスク</h3><ul><li>円高：ファンド別の通貨・為替ヘッジ比率がないため影響額は算出しません。</li><li>米国大型テック下落：同一基準日の組入銘柄と重複率がないため、S&amp;P500・オルカン・FANG+を重複計上せずに試算できません。</li><li>Goldあり/なし：Goldは現保有に含まれず、C/D案は新規積立額の構成だけを比較しています。資産全体のGoldストレスは算出していません。</li></ul></section>'
    return body


def render_data_methodology(info):
    from history_report import render_history

    snapshot, assumptions = info['snapshot'], info['assumptions']
    diagnosis = info['diagnosis']
    nisa = assumptions['nisa_snapshot']
    ref = assumptions['handover_reference']
    gain_gap = snapshot['totals']['gain_yen'] - ref['fund_gain_yen']
    body = '<section id="methodology"><h2>Data &amp; Methodology</h2>'
    body += simple_table(['入力・方法', '出典日/前提', '扱い'], [
        ['保有CSV', snapshot['export_timestamp_inferred'][:10] + '（出力日時推定 ' + snapshot['export_timestamp_inferred'][11:] + '）', '価格評価基準日時は不明'],
        ['注文履歴', info['history']['orders']['metadata']['発注開始年月日'] + ' ～ ' + info['history']['orders']['metadata']['発注終了年月日'], '発注記録'],
        ['約定履歴', info['history']['trades']['metadata']['約定開始年月日'] + ' ～ ' + info['history']['trades']['metadata']['約定終了年月日'], '受渡金額の買付実績'],
        ['NISA画面転記', nisa['source_date'], '画面の最大利用可能額を正とし、CSV等から再推計しない'],
        ['候補コスト・重複', info['fund_candidates']['source_date'], 'ユーザー入力の未検証目安'],
    ])
    body += '<h3>計算と限界</h3><ul><li>取得金額と評価損益は保有明細のみを合算。取得金額は累計入金額ではありません。</li><li>目標試算は月末拠出、一定年率、実効年率を月率へ換算。税・費用・分配・為替を考慮しません。</li><li>資産クラス概算は8資産均等型の基本比率を適用し、その他ファンドを株式として計算。</li><li>NISA利用状況は画面転記、NISA口座の現在残高は保有CSV。現在残高を年間・生涯枠利用額とみなしません。</li><li>現保有ファンドの費用率、正確な地域・銘柄重複、NISA残枠の将来変化は未確認です。</li></ul>'
    external = info.get('external_data', {})
    external_rows = []
    for record in external.get('records', []):
        value = record.get('value') if record.get('status') == 'available' else None
        if isinstance(value, list):
            display_value = f'系列 {len(value)}点' if value else '空系列'
        else:
            display_value = json.dumps(value, ensure_ascii=False, separators=(',', ':')) if value is not None else 'unavailable'
        external_rows.append([
            record.get('metric', 'unknown'),
            record.get('subject', 'unknown'),
            record.get('status', 'unavailable'),
            display_value,
            record.get('as_of') or 'unavailable',
            record.get('fetched_at') or 'unavailable',
            record.get('expires_at') or 'unavailable',
            str(record.get('age_days')) + ' days' if record.get('age_days') is not None else 'unavailable',
            record.get('freshness_status', 'stale' if record.get('stale', True) else 'fresh'),
            record.get('confidence', 'unrated'),
            record.get('provider_name', 'unknown'),
            record.get('source_url') or 'unavailable',
            record.get('license_status', 'unreviewed'),
            record.get('report_period') or 'n/a',
        ])
    body += '<h3>外部データ取得状況</h3><p>取得は既定で無効です。実行時指定、source registryでの有効化、規約/ライセンス承認、request budgetとinterval、resource、必要なAPI keyがすべて揃った場合のみ取得します。公開許可が不明なraw/derived valueはここに値を出しません。</p>'
    if external_rows:
        body += simple_table(['metric', 'subject', 'status', 'value', 'as_of', 'fetched_at', 'expires_at', 'age', 'freshness', 'confidence', 'provider', 'source_url', 'license', 'report_period'], external_rows)
    else:
        body += '<p>外部providerのデータ記録はありません。</p>'
    body += '<p class="muted">availability: ' + str(external.get('available_count', 0)) + ' available / ' + str(external.get('unavailable_count', 0)) + ' unavailable. 公開許可済みの月次snapshotは `data/market/` に保存されGit管理対象外です。raw responseは明示許可がない限り保持しません。</p>'
    body += '<h3>公式資料</h3><ul>'
    for item in diagnosis['sources']:
        body += '<li><a href="' + escape(item['url'], quote=True) + '">' + escape(item['title']) + '</a>（確認日 ' + escape(item['accessed']) + '）</li>'
    for fund, details in info['fund_candidates']['nisa_eligibility'].items():
        body += '<li><a href="' + escape(details['source_url'], quote=True) + '">' + escape(fund) + ' NISA表示</a>（確認日 ' + escape(details['verified_date']) + '）</li>'
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