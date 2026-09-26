"""HTML sections for execution evidence and provisional portfolio recommendations."""
from datetime import date, datetime
from html import escape
from reporting import yen, pct


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


def render_goal_and_funds(info):
    snapshot = info['snapshot']
    goal = info['assumptions']['goal_tracker']
    candidate_input = info['fund_candidates']
    start_value = snapshot['totals']['value_yen']
    as_of = snapshot['export_timestamp_inferred']
    target_date = date(goal['birth_year'] + goal['target_age'], goal['birth_month'], 1)
    target_label = f'{target_date.year}年{target_date.month}月'
    target_amount = '1億円' if goal['target_yen'] == 100000000 else yen(goal['target_yen']) + '円'
    body = f'<section id="goal"><h2>{goal["target_age"]}歳で{target_amount}を目指す積立案</h2>'
    body += f'<p>本人申告：{goal["birth_year"]}年{goal["birth_month"]}月生まれ。年齢は誕生月単位で概算し、目標時点は{target_label}、積立はその前月までとして計算します。</p>'
    steps = goal['contributions_yen']
    body += simple_table(['期間', '月額', '位置づけ'], [['現在～25歳', yen(steps['through_age_25']) + '円', '本人の想定'], ['26～29歳', yen(steps['age_26_to_29']) + '円', '本人の想定'], ['30歳以降', yen(steps['age_30_plus_base']) + '円 / ' + yen(steps['age_30_plus_tentative']) + '円', '25万円は検討中の比較ケース']])
    body += '<p>現在の出発点は、今回の保有状況CSVにある投資信託評価額 ' + yen(start_value) + '円（出力日 ' + escape(as_of[:10]) + '）。銀行残高は含みません。CSVにはジュニアNISAを含み、資産の名義・本人帰属が未確認のため、全額を本人の目標資産に算入できるとは限りません。</p>'
    body += '<p class="muted">試算は毎月末積立、月次複利、税・費用なし、評価額が一定率で増減する単純モデルです。年率は予測や期待リターンではありません。各シナリオは目標達成を保証せず、価格変動・為替・積立継続可否・家計状況を反映しません。</p>'
    scenarios = []
    for rate in goal['annual_return_scenarios_pct']:
        base = goal_projection(start_value, as_of, rate, False, goal)
        increased = goal_projection(start_value, as_of, rate, True, goal)
        scenarios.append([f'{rate}%', yen(base['value_yen']) + '円', yen(increased['value_yen']) + '円'])
    body += simple_table(['仮定年率', '26歳以降 月20万円', '30歳以降 月25万円（検討中）'], scenarios)
    base = goal_projection(start_value, as_of, 0, False, goal)
    increased = goal_projection(start_value, as_of, 0, True, goal)
    base_return = required_goal_return(start_value, as_of, False, goal)
    increased_return = required_goal_return(start_value, as_of, True, goal)
    body += '<p>積立元本の追加分は、月20万円継続で ' + yen(base['contributions_yen']) + '円、30歳から月25万円の場合で ' + yen(increased['contributions_yen']) + '円です。1億円に届くための計算上の一定年率は、それぞれ約' + f'{base_return:.2f}%' + '、約' + f'{increased_return:.2f}%' + 'となります。これは達成可能性や適切なリスク水準を示すものではありません。</p>'
    body += '<h3>配分シナリオ A～D</h3><p>以下は1億円目標に向けた新規積立の配分比較です。いずれも未採用で、購入推奨ではありません。A～Dのどれかを唯一の正解として選ぶのではなく、目標額・継続可能な積立額・許容できる値下がりから検討します。</p>'
    amount_levels = [
        ('月15万円', steps['through_age_25']),
        ('月20万円', steps['age_26_to_29']),
        ('月25万円（30歳以降・検討中）', steps['age_30_plus_tentative']),
    ]
    plans = [
        ('案A：成長株を上乗せ', 'オルカンを中心に、NASDAQ100と米国中小型株を追加。', [('eMAXIS Slim オルカン', 60), ('eMAXIS Slim S&P500', 20), ('ニッセイNASDAQ100', 10), ('Tracers S&P1000', 10)]),
        ('案B：地域分散', 'NASDAQ100を使わず、S&P1000と全世界（除く米国）を加える。', [('eMAXIS Slim オルカン', 60), ('eMAXIS Slim S&P500', 20), ('Tracers S&P1000', 10), ('SBI・V・全世界（除く米国）', 10)]),
        ('案C：Gold 10%', '株式中心に金を10%組み合わせる。', [('eMAXIS Slim オルカン', 60), ('eMAXIS Slim S&P500', 20), ('ニッセイNASDAQ100', 10), ('SBI-iShares Gold Hなし', 10)]),
        ('案D：Gold 5%', '新規積立の株式を95%、金を5%とする。', [('eMAXIS Slim オルカン', 65), ('eMAXIS Slim S&P500', 20), ('ニッセイNASDAQ100', 10), ('SBI-iShares Gold Hなし', 5)]),
    ]
    for title, description, allocations in plans:
        body += f'<h4>{escape(title)}</h4><p>{escape(description)}</p>'
        headers = ['ファンド', '比率'] + [label for label, _ in amount_levels]
        rows = [[name, f'{weight}%'] + [yen(amount * weight // 100) + '円' for _, amount in amount_levels] for name, weight in allocations]
        body += simple_table(headers, rows)
    fang_value = sum(r['value_yen'] for r in snapshot['holdings'] if 'FANG+' in r['fund'])
    body += '<p>既存FANG+は今回のCSVで ' + yen(fang_value) + '円です。新規積立を止める案でも保有分は残り、その扱いは別途判断します。現在の8資産均等型の保有も残るため、D案の「株式95%＋Gold5%」は新規積立額の構成であり、資産全体の構成ではありません。</p>'
    body += '<p class="notice">積立額の段階的な増額自体が目標達成に向けた大きな要素です。1億円を理由にFANG+などへ集中を高める必要がある、とこの試算から結論づけることはできません。30歳以降の25万円、本人帰属資産の範囲、現金・近い将来の支出を確認してから案を選ぶ前提です。</p></section>'
    body += '<section id="fund-universe"><h2>ファンド候補の比較入力</h2><p>重点比較候補はオルカン、S&amp;P500、NASDAQ100、S&amp;P1000、全世界（除く米国）、Gold Hなしの6本です。以下の一覧はユーザー提供の検討用入力で、コスト・重複・分散評価は未検証の目安です。原入力は <a href="../../docs/fund_candidates.json">docs/fund_candidates.json</a> に保存しています。</p>'
    body += '<details><summary>候補21本と評価軸を表示</summary>'
    body += simple_table(['ファンド', '投資対象', 'コスト目安（入力）', '既存PFとの重複', '分散効果', '主な役割', '検討位置'], [[r['fund'], r['investment_target'], r['cost_estimate'], r['existing_overlap'], r['diversification'], r['main_role'], r['consideration']] for r in candidate_input['items']])
    body += '<p class="muted">' + escape(candidate_input['status_note']) + ' SBI-iShares全世界債券は、入力値の約0.14～0.16%に対し、2026-09-26確認の公式資料では実質約0.1838%程度と差があり、要再確認です。<a href="https://www.sbiam.co.jp/fund/memo/sa_2013051304.html">SBIアセットマネジメント公式費用資料</a></p></details></section>'
    return body


def render_diagnosis(info):
    d, s = info['diagnosis'], info['snapshot']
    source = lambda i: '<a href="' + escape(d['sources'][i]['url'], quote=True) + '">' + escape(d['sources'][i]['title']) + '</a>'
    body = '<section id="diagnosis"><h2>ポートフォリオ診断</h2><h3>成長性を重視した構成。今後の集中を調整する余地があります</h3>'
    body += '<p>現在の積立設定はS&amp;P500、オール・カントリー、FANG+が各月5万円という前提です。FANG+は投資信託評価額の11.07%を占め、現在の設定を続けると新規積立の3分の1が同商品に向かいます。集中投資リスクは運用会社も説明しています。' + source(0) + '</p>'
    body += '<p>オール・カントリーを積立の中心にすると、米国への投資を残しながら他の先進国・新興国への投資も増やせます。ただし、S&P500やFANG+との銘柄重複を解消する商品ではありません。正確な重複率・米国比率は、同じ基準日の組入明細が未取得のため算出していません。' + source(1) + '</p>'
    body += '<h3>8資産均等型の27%を、そのまま安全資産とは見なしません</h3><p>基本配分は株式3資産・債券3資産・REIT2資産が各12.5%です。この基本配分と、その他の保有ファンドを株式100%とする仮定で分解すると、投資信託全体は次の概算になります。実際の当日組入比率ではありません。' + source(2) + '</p>'
    body += simple_table(['資産区分（推計）', '投資信託全体に占める割合'], [[r['name'], pct(r['weight_pct'])] for r in d['asset_estimate']])
    body += '<p>債券・REITにも値下がりや為替変動があります。生活費や近い将来の支出に備える現金とは分けて管理します。</p>'
    body += '</section>'
    body += render_goal_and_funds(info)
    body += '<section><h2>下落への耐性を金額で確認</h2><p>次は仮定の同時下落シナリオです。過去の最悪値、発生確率、最大損失の上限ではありません。為替変動は別途上乗せせず、円建ての商品価格の変動として仮定しています。</p>'
    body += simple_table(['商品グループ', '仮定する下落率', '評価額の変化（円）'], [[r['category'], str(r['shock_pct']) + '%', yen(r['change_yen'], True)] for r in d['stress']])
    body += f'<p><strong>合計 {yen(d["stress_change_yen"], True)}円（{d["stress_change_pct"]}%）、残る評価額は {yen(s["totals"]["value_yen"] + d["stress_change_yen"])}円。</strong>この規模の下落でも生活と積立を維持できるかを、A～D案を選ぶ前に確認してください。</p></section>'
    body += '<section><h2>公式資料と判断の区別</h2><p>以下の公式資料を2026年9月26日に参照しました。商品構造は資料に基づき、目標積立案・配分シナリオ・下落率は本人申告または本レポートの試算仮定です。将来の収益や優劣は確約しません。</p><ul>'
    for item in d['sources']:
        body += '<li><a href="' + escape(item['url'], quote=True) + '">' + escape(item['title']) + '</a>：' + escape(item['claim']) + '</li>'
    return body + '</ul></section>'
