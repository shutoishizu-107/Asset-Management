"""Self-contained offline HTML reports with tables and interactive charts."""
from html import escape
from analyze_holdings import percentage

COLORS = ['#246bce', '#087e8b', '#9a6617', '#9052bb', '#d05d43', '#576477']
STYLE = '''
:root{color-scheme:light;--ink:#17263b;--muted:#56657a;--line:#dce3ed;--bg:#f4f7fb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 "Yu Gothic",Meiryo,system-ui,sans-serif}
main{max-width:1180px;margin:auto;padding:38px 24px 70px}header{margin-bottom:24px}h1{font-size:32px;line-height:1.35;margin:8px 0 12px}h2{font-size:21px;margin:0 0 16px}h3{font-size:16px;margin:24px 0 10px}
.eyebrow{font-size:12px;letter-spacing:.13em;color:#246bce;font-weight:700}.muted,small{color:var(--muted)}a{color:#1855a2}p{margin:10px 0}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card,section{background:white;border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:20px}.card .label{font-size:13px;color:var(--muted)}.number{font-size:25px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}.unit{font-size:14px;margin-left:4px}
.notice{padding:14px 18px;border-left:4px solid #b37a16;background:#fff5dd;border-radius:4px;margin:18px 0}.ok{color:#17604b}.negative{color:#a63038}.positive{color:#17604b}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:12px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}th{background:#f2f5fa;color:#41536b;font-weight:600}th:first-child,td:first-child{text-align:left;white-space:normal;min-width:150px}tbody tr:hover{background:#f8faff}.fund-table td:first-child{min-width:300px}.total td{font-weight:700;background:#eef3fa}.two{display:grid;grid-template-columns:1fr 1fr;gap:24px}.barrow{margin:18px 0}.barhead{display:flex;justify-content:space-between;gap:15px;font-size:14px}.track{height:14px;background:#edf1f7;border-radius:4px;overflow:hidden;margin-top:6px}.fill{height:100%;border-radius:4px}.slider{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin:18px 0}select{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:5px;max-width:100%}input[type=range]{width:min(450px,90vw);accent-color:#246bce}output{font-weight:700}.mono{font-family:ui-monospace,monospace;overflow-wrap:anywhere;font-size:12px}details{margin:18px 0}summary{cursor:pointer;font-weight:600}footer{color:var(--muted);font-size:12px}nav{display:flex;gap:16px;flex-wrap:wrap}li{margin:6px 0}
@media(max-width:800px){.cards{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}main{padding:22px 14px}.number{font-size:22px}h1{font-size:26px}section{padding:18px}}
@media(max-width:430px){.cards{grid-template-columns:1fr}.card{margin-bottom:0}.cards{margin-bottom:18px}}
@media print{body{background:white}main{max-width:none;padding:0}section,.card{break-inside:avoid;border-color:#bbb}nav,.slider{display:none}.scroll{overflow:visible}table{font-size:10px}th,td{padding:5px;white-space:normal}.two{grid-template-columns:1fr 1fr}a{color:inherit}.fill{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
'''


def yen(value, signed=False):
    return f'{value:+,}' if signed else f'{value:,}'


def pct(value):
    return '—' if value is None else str(value) + '%'


def page(title, body, script=''):
    return '<!doctype html>\n<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>' + STYLE + '</style></head><body><main>' + body + '</main>' + script + '</body></html>\n'


def bars(rows):
    result = []
    for index, row in enumerate(rows):
        weight = row['weight_pct'] or '0'
        result.append(f'<div class="barrow"><div class="barhead"><span>{escape(row["name"])}</span><strong>{weight}%</strong></div><div class="track"><div class="fill" style="width:{weight}%;background:{COLORS[index % len(COLORS)]}"></div></div><small>{yen(row["value_yen"])} 円</small></div>')
    return ''.join(result)


def data_table(rows, label, total=None):
    head = f'<div class="scroll"><table class="fund-table"><thead><tr><th>{label}</th><th>評価額（円）</th><th>取得金額（円）</th><th>評価損益（円）</th><th>損益率</th><th>構成比</th></tr></thead><tbody>'
    body = []
    for row in rows:
        gain = row['gain_yen']
        body.append(f'<tr><td>{escape(row["name"])}</td><td>{yen(row["value_yen"])}</td><td>{yen(row["cost_yen"])}</td><td class="{"negative" if gain < 0 else "positive"}">{yen(gain, True)}</td><td>{pct(row["gain_pct"])}</td><td>{pct(row["weight_pct"])}</td></tr>')
    if total:
        body.append(f'<tr class="total"><td>合計</td><td>{yen(total["value_yen"])}</td><td>{yen(total["cost_yen"])}</td><td>{yen(total["gain_yen"], True)}</td><td>{pct(total["gain_pct"])}</td><td>100.00%</td></tr>')
    return head + ''.join(body) + '</tbody></table></div>'


def header(snapshot, title):
    return f'<header><div class="eyebrow">ASSET MANAGEMENT / LOCAL REPORT</div><h1>{title}</h1><p>出力日時（元ファイル名から推定）：{escape(snapshot["export_timestamp_inferred"].replace("T", " "))}</p><p class="muted">評価基準日時はCSV本文に記載なし。月末確定値ではありません。</p></header>'


def charts_body(snapshot):
    return '<div class="two"><div><h3>商品グループ別の配分</h3>' + bars(snapshot['categories']) + '</div><div><h3>口座区分別の配分</h3>' + bars(snapshot['accounts']) + '</div></div>'


def render_charts(info):
    s = info['snapshot']
    return page('資産配分グラフ', header(s, '資産配分グラフ') + '<section>' + charts_body(s) + '<p class="muted">分母はCSV内の投資信託評価額。銀行預金を含みません。商品グループはファンド名による分類で、内部の地域・資産配分ではありません。</p></section><footer>出典：' + escape(s['source_file']) + '</footer>')


def render_report(info, tag):
    s, assumptions = info['snapshot'], info['assumptions']
    t, ref = s['totals'], assumptions['handover_reference']
    groups = {r['name']: r for r in s['categories']}
    sp = groups.get('S&P500系', {}).get('value_yen', 0)
    fang = groups.get('FANG+', {}).get('value_yen', 0)
    gain_gap = t['gain_yen'] - ref['fund_gain_yen']
    body = header(s, '保有資産 分析レポート')
    body += '<nav><a href="#allocation">配分</a><a href="#funds">ファンド別</a><a href="#accounts">口座別</a><a href="#history">注文・約定</a><a href="#diagnosis">リスク診断</a><a href="#goal">1億円の積立案</a><a href="#fund-universe">候補一覧</a><a href="#quality">確認事項</a></nav><br><div class="cards">'
    for label, value, unit in [('投資信託 評価額', yen(t['value_yen']), '円'), ('取得金額', yen(t['cost_yen']), '円'), ('評価損益', yen(t['gain_yen'], True), '円'), ('取得金額に対する損益率', t['gain_pct'] or '—', '%')]:
        body += f'<div class="card"><div class="label">{label}</div><div class="number">{value}<span class="unit">{unit}</span></div></div>'
    body += '</div><section><h2>今回確認できたこと</h2><ul>'
    body += f'<li>{len(s["holdings"])}明細・{len(s["funds"])}ファンド・{len(s["accounts"])}口座区分を集計しました。取得金額・評価損益を含む明細と、CSV全体・口座別集計との整合性を確認済みです。</li>'
    body += f'<li>S&P500系の商品は合計 {yen(sp)}円（{percentage(sp, t["value_yen"])}%）。FANG+との商品評価額の合計は {percentage(sp + fang, t["value_yen"])}% です。これは商品別の合算であり、実質的な米国比率や重複銘柄比率ではありません。</li>'
    body += f'<li>CSV記載の前日比は {yen(t["day_change_yen"], True)}円。取得金額に対する損益率は年率・年初来リターンではありません。</li><li>ジュニアNISAを含むCSV全体を集計しています。口座名義の情報はなく、個人別・家族別の切り分けは未確認です。</li></ul></section>'
    body += '<section id="allocation"><h2>現在の配分</h2>' + charts_body(s) + '<p class="muted">分母：投資信託評価額。銀行残高は含めていません。8資産均等型・オール・カントリー等の内部構成は分解していません。</p></section>'
    body += '<section id="funds"><h2>ファンド別の評価額・損益</h2>' + data_table(s['funds'], 'ファンド', t) + '<p class="muted">損益率＝評価損益÷取得金額。構成比＝評価額÷投資信託全体の評価額。率は小数第2位に丸めており、構成比の表示合計が100%からずれる場合があります。</p></section>'
    body += '<section id="accounts"><h2>口座区分別の評価額・損益</h2>' + data_table(s['accounts'], '口座区分', t) + '<p class="muted">口座区分はCSVの見出しから取得しています。取得金額は現在の保有分であり、年間の買付額・NISA枠の残額ではありません。</p></section>'
    from history_report import render_history, render_diagnosis
    body += render_history(info)
    body += render_diagnosis(info)
    body += '<section><h2>前回データとの比較</h2>'
    if info['comparison'] is None:
        body += '<p>独立した出力時点は1件のため、今回は比較できません。次の日時のCSVを追加すると、前回出力時点との評価額・取得金額・評価損益・口数の差分を生成します。</p>'
    else:
        c = info['comparison']
        body += '<p>前回：' + escape(c['previous_source']) + '。' + escape(c['note']) + '</p><div class="scroll"><table><thead><tr><th>口座 / ファンド</th><th>評価額差（円）</th><th>取得金額差（円）</th><th>評価損益差（円）</th><th>口数差</th></tr></thead><tbody>'
        for row in c['rows']:
            body += '<tr><td>' + escape(row['account'] + ' / ' + row['fund']) + '</td>' + ''.join('<td>' + yen(row[key], True) + '</td>' for key in ('value_yen_delta', 'cost_yen_delta', 'gain_yen_delta', 'units_delta')) + '</tr>'
        body += '</tbody></table></div>'
    body += '</section>'
    body += '<section id="quality"><h2>データ品質と参照情報</h2><p class="ok">整合性チェック：合格。各明細の「評価額 − 取得金額＝評価損益」、口座別件数、口座別・全体の評価額・評価損益・前日比を照合しています。</p>'
    body += f'<div class="notice">引継ぎ文書（{escape(assumptions["source_date"])}）の投資信託評価損益は {yen(ref["fund_gain_yen"], True)}円、今回のCSVは {yen(t["gain_yen"], True)}円。差は {yen(gain_gap, True)}円です。差の原因は未確認です。後日のCSVでは時点差も含む比較になります。</div>'
    body += f'<p>引継ぎ文書だけにある参考情報：銀行残高 {yen(ref["bank_balance_yen"])}円、総資産 {yen(ref["total_assets_yen"])}円（{escape(assumptions["source_date"])}）。今回のCSVに銀行残高がないため、レポートの主要数値には加えていません。</p>'
    body += '<ul><li>現在のCSVは ' + escape(s['source_encoding']) + ' として読み取りました。元データの文字コードは変更していません。</li><li>積立注文・約定履歴を別途取り込み済みです。入出金履歴・期首評価額・全期間の取得原価・ファンド内部の最新組入明細は未取得です。年初来リターン、最大ドローダウン、実現損益、実質米国比率は未確定です。</li><li>許可を受けて公開公式資料を参照しました。履歴ファイル・口座情報は外部送信していません。売買注文は行っていません。</li></ul>'
    body += '<details><summary>保有明細と出典を確認</summary><div class="scroll"><table><thead><tr><th>口座 / ファンド</th><th>口数</th><th>評価額（円）</th><th>取得金額（円）</th><th>評価損益（円）</th><th>積立設定表示</th></tr></thead><tbody>'
    for h in s['holdings']:
        body += '<tr><td>' + escape(h['account'] + ' / ' + h['fund']) + '</td>' + ''.join('<td>' + yen(h[key], key == 'gain_yen') + '</td>' for key in ('units', 'value_yen', 'cost_yen', 'gain_yen')) + '<td>' + escape(h['accumulation_marker'] or '空欄') + '</td></tr>'
    body += '</tbody></table></div><p>入力：' + escape(s['source_file']) + '</p><p class="mono">SHA-256: ' + s['source_sha256'] + '</p></details></section>'
    body += f'<footer>出典：<a href="../../data/raw/csv/{escape(s["source_file"], quote=True)}">保有状況CSV</a> / <a href="../../docs/handover.md">引継ぎ文書</a> / <a href="../../docs/analysis_assumptions.json">試算前提</a> / <a href="../../docs/fund_candidates.json">ファンド比較入力</a> / <a href="../../data/processed/{tag}_analysis.json">分析結果JSON</a>。HTMLの表示と試算は外部通信なしで動作します。公式資料リンクを開く場合はWebにアクセスします。</footer>'
    return page('保有資産 分析レポート ' + s['export_timestamp_inferred'][:10], body)


def render_index(info, tag):
    s = info['snapshot']
    body = header(s, '資産運用レポート')
    body += '<section><h2>最新レポート</h2><p><a href="monthly/資産分析_' + tag[:6] + '.html">保有資産の分析・注文と約定の実績・1億円目標の積立案を見る</a></p><p><a href="charts/資産配分_' + tag[:6] + '.html">資産配分グラフを開く</a></p></section>'
    return page('資産運用レポート', body)
