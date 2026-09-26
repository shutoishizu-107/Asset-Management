"""Self-contained offline HTML reports with tables and interactive charts."""
from html import escape

COLORS = ['#246bce', '#087e8b', '#9a6617', '#9052bb', '#d05d43', '#576477']
STYLE = '''
:root{color-scheme:light;--ink:#17263b;--muted:#56657a;--line:#dce3ed;--bg:#f4f7fb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 "Yu Gothic",Meiryo,system-ui,sans-serif}
.eyebrow{font-size:12px;letter-spacing:.13em;color:#246bce;font-weight:700}.muted,small{color:var(--muted)}a{color:#1855a2}p{margin:10px 0}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.card,section{background:white;border:1px solid var(--line);border-radius:8px;padding:20px;margin-bottom:16px}.card .label{font-size:13px;color:var(--muted)}.number{font-size:25px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}.unit{font-size:14px;margin-left:4px}.button-link{display:inline-block;background:#1855a2;color:white;padding:8px 14px;border-radius:4px;text-decoration:none}
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
    nav = '<nav><a href="../../index.html">Dashboard</a><a href="../index.html">月次レポート一覧</a></nav>'
    return page('資産配分グラフ', header(s, '資産配分グラフ') + nav + '<section>' + charts_body(s) + '<p class="muted">分母はCSV内の投資信託評価額。銀行預金を含みません。商品グループはファンド名による分類で、内部の地域・資産配分ではありません。</p></section><footer>出典：' + escape(s['source_file']) + '</footer>')


def render_report(info, tag):
    s = info['snapshot']
    from report_sections import (
        render_contribution_plans, render_current_portfolio, render_data_methodology,
        render_executive_summary, render_existing_assets, render_fund_comparison,
        render_goal_tracker, render_nisa_strategy, render_portfolio_diagnosis,
        render_portfolio_options, render_risk_scenarios,
    )
    body = header(s, '資産運用分析レポート')
    body += render_executive_summary(info)
    body += '<nav><a href="../../index.html">Dashboard</a><a href="../index.html">月次レポート一覧</a><a href="#goal">1億円目標</a><a href="#portfolio">現在の保有</a><a href="#diagnosis">診断</a><a href="#nisa">NISA</a><a href="#funds">ファンド候補</a><a href="#portfolio-options">配分案</a><a href="#existing-assets">既存資産</a><a href="#risk">リスク</a><a href="#methodology">計算根拠</a></nav>'
    body += render_goal_tracker(info)
    body += render_current_portfolio(info)
    body += render_portfolio_diagnosis(info)
    body += render_nisa_strategy(info)
    body += render_fund_comparison(info)
    body += render_portfolio_options()
    body += render_contribution_plans(info)
    body += render_existing_assets(info)
    body += render_risk_scenarios(info)
    body += render_data_methodology(info)
    body += f'<footer><a href="../../data/raw/csv/{escape(s["source_file"], quote=True)}">保有状況CSV</a> / <a href="../../README.md">Developer README</a> / <a href="../../config/profile.json">Profile</a> / <a href="../../config/goals.json">Goals</a> / <a href="../../docs/analysis_assumptions.json">NISA・分析前提</a> / <a href="../../data/processed/{tag}_analysis.json">分析JSON</a>。HTMLは外部通信なしで表示できます。</footer>'
    return page('資産運用分析レポート ' + s['export_timestamp_inferred'][:10], body)


def render_dashboard(info, tag):
    from report_sections import render_dashboard_content
    return page('Asset Management Dashboard', render_dashboard_content(info, tag))


def render_index(info, tag, monthly_reports=(), chart_reports=()):
    snapshot = info['snapshot']
    body = header(snapshot, '月次レポート一覧')
    body += '<nav><a href="../index.html">Dashboard</a></nav><section><h2>資産分析</h2>'
    rows = []
    for name in monthly_reports:
        month = name.removeprefix('資産分析_').removesuffix('.html')
        chart = '資産配分_' + month + '.html'
        chart_link = '<a href="charts/' + escape(chart, quote=True) + '">配分チャート</a>' if chart in chart_reports else '未生成'
        rows.append([month[:4] + '年' + str(int(month[4:6])) + '月', '<a href="monthly/' + escape(name, quote=True) + '">資産分析を開く</a>', chart_link])
    body += _archive_table(rows) + '</section>'
    return page('月次レポート一覧', body)


def _archive_table(rows):
    return '<div class="scroll"><table><thead><tr><th>対象月</th><th>月次分析</th><th>資産配分</th></tr></thead><tbody>' + ''.join('<tr><td>' + row[0] + '</td><td>' + row[1] + '</td><td>' + row[2] + '</td></tr>' for row in rows) + '</tbody></table></div>'
