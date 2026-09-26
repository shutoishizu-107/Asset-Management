"""HTML sections for execution evidence and provisional portfolio recommendations."""
from html import escape
from reporting import yen, pct, bars
from analyze_holdings import percentage


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


def render_diagnosis(info):
    d, s = info['diagnosis'], info['snapshot']
    current = {r['name']: r for r in s['categories']}
    scenarios = d['scenarios']
    source = lambda i: '<a href="' + escape(d['sources'][i]['url'], quote=True) + '">' + escape(d['sources'][i]['title']) + '</a>'
    body = '<section id="diagnosis"><h2>ポートフォリオ診断</h2><h3>成長性を重視した構成。今後の集中を調整する余地があります</h3>'
    body += '<p>長期・月次積立という方針は履歴でも確認できました。一方で、今の新規積立の3分の1がFANG+に向かう配分を続けると、保有比率の11%程度を超えて集中が強まります。10銘柄に集中する商品の位置付けを、資産全体の中心ではなく追加の成長枠に限定する案を推奨します。FANG+の集中投資リスクは運用会社も明示しています。' + source(0) + '</p>'
    body += '<p>オール・カントリーを積立の中心にすると、米国への投資を残しながら他の先進国・新興国への投資も増やせます。ただし、S&P500やFANG+との銘柄重複を解消する商品ではありません。正確な重複率・米国比率は、同じ基準日の組入明細が未取得のため算出していません。' + source(1) + '</p>'
    body += '<h3>8資産均等型の27%を、そのまま安全資産とは見なしません</h3><p>基本配分は株式3資産・債券3資産・REIT2資産が各12.5%です。この基本配分と、その他の保有ファンドを株式100%とする仮定で分解すると、投資信託全体は次の概算になります。実際の当日組入比率ではありません。' + source(2) + '</p>'
    body += simple_table(['資産区分（推計）', '投資信託全体に占める割合'], [[r['name'], pct(r['weight_pct'])] for r in d['asset_estimate']])
    body += '<p>債券・REITにも値下がりや為替変動があります。生活費や近い将来の支出に備える現金とは分けて管理します。</p>'
    body += '</section>'
    body += '<section id="recommendation"><h2>今後の推奨ポートフォリオ（暫定案）</h2><p>' + escape(d['condition']) + '</p><p>' + escape(d['method']) + '</p>'
    body += '<h3>5年程度で近づける商品配分の目安</h3>'
    body += simple_table(['商品グループ', '現在', '中期目安'], [[n, pct(current.get(n, {}).get('weight_pct')), f'{v}%'] for n, v in d['target_pct'].items()])
    body += '<p class="muted">分母は投資信託全体、現金は別管理。目安は合計100%。インド株の既存分は保有を継続し、1%未満の残存を許容します。家族名義のジュニアNISA資産は移動せず、家族全体を集計する場合の目安として扱います。</p>'
    body += '<h3>毎月15万円の振り分け</h3>'
    categories = ['S&P500系', 'オール・カントリー', 'FANG+', '8資産均等型']
    body += simple_table(['積立案'] + categories, [[name] + [yen(plan.get(n, 0)) + '円' for n in categories] for name, plan in d['plans'].items()])
    body += '<p><strong>推奨案はオール・カントリー9万円、S&P500 4万5千円、FANG+ 1万5千円です。</strong>既存資産を保有しながら、新規積立で徐々に配分を変えます。インド株は今回の案では新規積立しません。これは新興国の将来性の予測ではなく、全世界株式を通じた保有に寄せるための整理です。</p>'
    recommended_balance = next(r for r in scenarios if r['plan'].startswith('推奨案') and r['months'] == 60 and r['category'] == '8資産均等型')
    from decimal import Decimal
    future_equity = percentage(Decimal(recommended_balance['total_yen']) - Decimal(recommended_balance['value_yen']) * Decimal('.625'), recommended_balance['total_yen'])
    body += '<p class="notice">集中の緩和は、総リスクの低下と同じではありません。推奨案でも8資産均等型への追加積立を止めるため、基本配分による株式比率の概算は現在約83%から5年後約' + future_equity + '%へ上がります。株式全体の下落に備える必要がある場合は、比較案や現金の確保を選んでください。</p>'
    body += '<p>推奨案も株式中心です。大きな下落への不安が強い場合は8資産均等型にも月3万円を回す比較案を検討し、さらに現金・債券の必要量を家計から決めます。生活防衛資金が未確保なら、月15万円の投資継続より現金確保を優先します。</p>'
    body += '<h3>新規積立で配分を変更した場合</h3><p class="muted">価格変動・分配・税・費用なしの機械的比較。将来リターン予測ではありません。</p>'
    names = [r['name'] for r in s['categories']]
    display = []
    for plan in d['plans']:
        for months in (12, 36, 60):
            part = {r['category']: r for r in scenarios if r['plan'] == plan and r['months'] == months}
            display.append([plan, str(months // 12) + '年後'] + [pct(part[n]['weight_pct']) for n in names])
    body += simple_table(['積立案', '期間'] + names, display)
    body += '<p>年1回、またはFANG+が15%を超えたときに配分を点検する運用ルールを提案します。値上がりだけを理由に積立額を増やさず、まず新規積立先で調整します。8資産均等型が15%程度まで下がったら、その後の積立にも配分して維持するか再検討します。これらの閾値も今回の提案です。</p>'
    body += '<p>NISA枠はこのCSVの保有取得金額から残額を推定せず、証券会社の利用可能枠を確認して振り分けます。制度の確認先：' + source(4) + '</p></section>'
    body += '<section><h2>下落への耐性を金額で確認</h2><p>次は仮定の同時下落シナリオです。過去の最悪値、発生確率、最大損失の上限ではありません。為替変動は別途上乗せせず、円建ての商品価格の変動として仮定しています。</p>'
    body += simple_table(['商品グループ', '仮定する下落率', '評価額の変化（円）'], [[r['category'], str(r['shock_pct']) + '%', yen(r['change_yen'], True)] for r in d['stress']])
    body += f'<p><strong>合計 {yen(d["stress_change_yen"], True)}円（{d["stress_change_pct"]}%）、残る評価額は {yen(s["totals"]["value_yen"] + d["stress_change_yen"])}円。</strong>この規模の下落でも生活と積立を維持できるかを、推奨案採用前の判断基準にしてください。</p></section>'
    body += '<section><h2>公式資料と判断の区別</h2><p>以下の公式資料を2026年9月26日に参照しました。商品構造は資料に基づき、配分比率・待機資金・見直し基準・下落率は本レポートの提案または試算仮定です。将来の収益や優劣は確約しません。</p><ul>'
    for item in d['sources']:
        body += '<li><a href="' + escape(item['url'], quote=True) + '">' + escape(item['title']) + '</a>：' + escape(item['claim']) + '</li>'
    return body + '</ul></section>'
