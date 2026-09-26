"""Explicit advisory assumptions, scenario arithmetic, and dated public sources."""
from decimal import Decimal, ROUND_HALF_UP
from analyze_holdings import percentage

SOURCES = [
    {'title': '大和アセットマネジメント：FANG+の特徴・集中投資リスク', 'url': 'https://www.daiwa-am.co.jp/special/ifree/fang-1/', 'accessed': '2026-09-26', 'claim': '10銘柄に等金額投資する指数。特定銘柄への集中投資リスクを説明。構成銘柄の最新比率は今回取り込まない。'},
    {'title': 'SBI証券：eMAXIS Slim 全世界株式（オール・カントリー）', 'url': 'https://www.sbisec.co.jp/contents/lp/2026/acwi-feature.html', 'accessed': '2026-09-26', 'claim': '日本を含む先進国・新興国の株式への分散とMSCI ACWIへの連動を説明。'},
    {'title': '三菱UFJ銀行：eMAXIS Slim バランス（8資産均等型）', 'url': 'https://fs.bk.mufg.jp/webasp/mufg/fund/detail/m00355120.html', 'accessed': '2026-09-26', 'claim': '国内・先進国・新興国の株式と債券、国内・先進国REITの8資産に基本配分各12.5%。'},
    {'title': '大和アセットマネジメント：iFreeNEXT FANG+インデックス商品情報', 'url': 'https://www.daiwa-am.co.jp/funds/detail/3346/detail_top.html', 'accessed': '2026-09-26', 'claim': '本保有ファンドの識別。毎月分配型ではない。基準価額は1万口当たり。'},
    {'title': '金融庁：NISAを知る', 'url': 'https://www.fsa.go.jp/policy/nisa2/know/index.html', 'accessed': '2026-09-26', 'claim': '制度説明への参照。個人の利用可能枠・年間買付額は本レポートでは確定しない。'},
]
def diagnosis(snapshot, history):
    categories = {r['name']: r['value_yen'] for r in snapshot['categories']}
    total = snapshot['totals']['value_yen']
    balance = categories.get('8資産均等型', 0)
    # Strategic proportions, not the exact current holdings of the underlying fund.
    bonds = Decimal(balance) * Decimal('.375')
    reits = Decimal(balance) * Decimal('.25')
    equity = Decimal(total) - bonds - reits
    allocation = [dict(name=n, value_yen=str(v), weight_pct=percentage(v, total)) for n, v in [('株式（概算）', equity), ('債券（概算）', bonds), ('REIT（概算）', reits)]]
    shocks = {'S&P500系': -35, 'オール・カントリー': -30, 'FANG+': -50, '8資産均等型': -15, 'インド株': -40, 'その他': -35}
    stress = []
    for name, value in categories.items():
        shock = shocks[name]
        loss = int((Decimal(value) * Decimal(shock) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        stress.append(dict(category=name, shock_pct=shock, current_yen=value, change_yen=loss, after_yen=value + loss))
    return dict(sources=SOURCES, asset_estimate=allocation,
        stress=stress, stress_change_yen=sum(r['change_yen'] for r in stress),
        stress_change_pct=percentage(sum(r['change_yen'] for r in stress), total),
        condition='生活防衛資金・近い将来の支出を別途確保し、長期投資を継続できるかを配分選択前に確認する。',
        method='目標達成の収益は保証されず、積立額・運用期間・リスク許容度を分けて検討する。')
