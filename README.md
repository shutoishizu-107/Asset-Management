# Asset Management

個人資産の分析Dashboardと月次レポートを生成するローカルPythonプロジェクトです。READMEはDeveloper Docs、公開Dashboardはルートの `index.html`、月次分析の詳細は `reports/monthly/` を正本とします。投資背景・現在地・未確認事項は [handover](docs/handover.md) を参照してください。

## Setup

- Python 3.10以降
- 標準ライブラリのみ。パッケージのインストールと外部通信は不要
- 元データは `data/raw/` に配置し、入力ファイルを直接編集しない

## Deployment

生成コマンドはルートのDashboard `index.html`、`reports/monthly/` の月次分析、`reports/charts/` の配分チャート、`reports/index.html` の月次一覧を作ります。GitHub Pagesの `/` にDashboardを公開する場合、Repository SettingsのPages sourceを `Deploy from a branch`、branchを `main`、folderを `/(root)` に設定してください。READMEは開発手順用として残します。

月次HTML名は `資産分析_yyyymm.html`、チャート名は `資産配分_yyyymm.html`。年月は実行日ではなく、最新の保有状況CSVの推定出力月です。同月は上書きし、別月は保持します。月途中データを月末確定値とは表現しません。HTMLは単体で表示でき、CDN・外部フォント・外部データは読み込みません。公式資料リンクを開く場合のみWebへアクセスします。

## Data Import

| 入力 | 必要な範囲・用途 | 配置・命名 |
| --- | --- | --- |
| 保有状況CSV（必須） | 同じ商品・口座範囲の全明細。評価額、取得金額、口数、損益と配分を集計 | `data/raw/csv/保有状況_YYYYMMDD_YYYYMMDD.csv` |
| 積立買付注文履歴CSV（必須） | 選択した検索期間の全件。発注と約定の対応確認に使用 | `data/raw/csv/積立買付注文履歴_YYYYMMDD_YYYYMMDD.csv` |
| 約定履歴CSV（必須） | 選択した検索期間の全件。買付実績と口数の照合に使用 | `data/raw/csv/約定履歴_YYYYMMDD_YYYYMMDD.csv` |
| 前回の保有状況CSV | 前回比較を出す場合に必要。過去の保有CSVをrawに残す | 同上。最新と直前の2時点を自動比較 |
| プロフィール・目標条件 | 生年月、目標額・年齢、年齢別積立額 | `config/profile.json`, `config/goals.json` |
| 現在の積立設定・NISA画面値 | 商品別の月額、年間/生涯枠の使用額・残額、画面確認日 | `docs/analysis_assumptions.json` |
| ファンド候補比較の入力 | 候補商品、コスト目安、既存PFとの重複、分散効果、役割、検討位置。未検証の入力値は確認値と区別 | `docs/fund_candidates.json` |
| 元ファイルの出典情報 | 元名、取得したファイルのSHA-256、対象期間、確認できる出力日時 | `docs/data_sources.json` |

保有状況の開始日と終了日は同じ出力日です。履歴の日付はCSV本文の検索期間と一致させます。履歴は画面の一部だけでなく、選択期間の全明細を出力してください。文字コードはUTF-8（BOMあり・なし）とCP932に対応します。

現行処理は保有状況だけでは実行できず、注文・約定履歴も各1ファイル必要です。履歴の期間重複を自動結合しません。比較する月を含む期間を出力し、可能なら前月と同じ開始日にして終了日を延ばします。期間を変えた場合は、累計額の比較条件も変わることを明記します。注文月と約定月は一致しない場合があります。

任意の補足情報は、評価基準日時、銀行残高と確認日、生活防衛資金、予定支出、許容損失額、口座名義・目的です。CSVで確認できない値は参考情報として区別します。銀行残高を後日の投信評価額に自動加算しません。画像は `data/raw/screenshots/` に保存できますが、自動取込の対象ではありません。

## Generate Report

1. 上記3種類のCSVを取得し、元の内容を変更せず指定名で配置します。過去の保有状況CSVはrawに残してください。注文・約定履歴は各種類1ファイルだけをrawに置き、古い履歴は先に `data/history/originals/` へ保管します。同名の原本を上書きしないよう保管先を分けます。
2. `docs/data_sources.json` の `files` に新しいCSVの相対パスをキーとして追加します。元名、SHA-256、検索期間、確認できる出力時刻を記録します。既存の出典情報は保持します。
3. プロフィール・目標条件が変われば `config/profile.json` と `config/goals.json` を更新します。現在の積立設定やSBI NISA画面が変わった場合は `docs/analysis_assumptions.json` を更新し、画面の利用可能額を転記します。残枠を保有簿価・約定履歴から逆算しません。
4. プロジェクト直下で生成コマンドを実行します。処理は最新の保有状況を選び、ルートDashboard、月次HTML、加工CSV・JSON、保有スナップショット、月次レポート一覧を生成します。過去月をまとめて再生成する処理ではありません。
5. 生成結果の基準日、対象期間、金額、前回比較、積立前提、出典日を確認します。HTMLの表・グラフ・リンク・狭い画面での表示を確認し、古いファイル名や掲載対象外の記載が残っていないことを確認します。
6. 変更した前提・未確認事項だけを `docs/handover.md` に更新します。運用手順は本READMEへ集約し、重複管理を避けます。

SHA-256の確認例（プロジェクト直下で実行）：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'data/raw/csv/保有状況_20261031_20261031.csv'
```

出典情報の追加例（実ファイルの値に置き換え、`files` 内に追加）：

```json
"data/raw/csv/保有状況_20261031_20261031.csv": {
  "original_filename": "fundHoldings_20261031150000.csv",
  "sha256": "実ファイルのSHA-256を小文字で記載",
  "period_start": "20261031",
  "period_end": "20261031",
  "export_timestamp_inferred": "2026-10-31T15:00:00",
  "date_note": "出力日時は元ファイル名から推定。価格の評価基準日時は未確認。"
}
```

履歴にも元名・ハッシュ・期間を同様に記録します。出力時刻が分からない場合は時刻を作らず `export_timestamp_inferred` を省略します。保有CSVはその場合ファイル名の日付で処理します。出力日を価格の評価基準日と同一視しないでください。保有CSVのハッシュ不一致は処理を停止するため、原因を確認せず記録済みハッシュを差し替えないでください。

## Command Reference

Python 3.10以降、標準ライブラリのみ。通常生成とoffline生成はパッケージのインストールを必要としません。

```powershell
python -B scripts/generate_monthly_report.py --month 2026-09
```

External Data Cache節の`--refresh-external-data`、`--offline` optionもこのentry pointで利用できます。

このPCで確認済みのPythonを指定する場合：

```powershell
& 'C:\Users\shuto\AppData\Local\Programs\Python\Python314\python.exe' -B scripts/generate_monthly_report.py --month 2026-09
```

コード変更時のテスト：

```powershell
python -B -m unittest discover -s scripts -p 'test_*.py' -v
```

必要に応じて `--root`（入力プロジェクト）、`--output-root`（出力先）、`--assumptions`（積立前提JSON）、`--history-source-dir`（注文・約定CSVの明示的な入力先）を指定できます。通常の月次運用では指定不要です。

## Update Fund Data

- 現在の積立設定とSBI画面のNISA使用額は `docs/analysis_assumptions.json` に記録します。画像自体がプロジェクトにない場合は転記元と画面日を記し、残枠を履歴から再構築しません。
- 生年月は `config/profile.json`、1億円の目標と年齢別積立・リターンシナリオは `config/goals.json` に保存します。
- ファンド候補のコスト・重複・分散効果は `docs/fund_candidates.json` で入力情報と公式確認値を区別します。NISA対象枠・販売会社の取扱は商品ごとに確認します。

## External Data Providers

通常のレポート生成はcache-firstです。月次snapshotがあれば同じ月のsnapshotを優先し、なければmetric TTL内のcacheを読みます。cache miss/expired metricだけを、`sources.yaml`で有効化・規約承認済みのproviderから取得します。

```powershell
python -B scripts/generate_monthly_report.py --month 2026-09
```

外部データの強制更新:

```powershell
python -B scripts/generate_monthly_report.py --month 2026-09 --refresh-external-data
```

オフライン実行はproviderへアクセスせず、月次snapshot、次にcacheだけを利用します。両方に値がなければ`unavailable`です。

```powershell
python -B scripts/generate_monthly_report.py --month 2026-09 --offline
```

metric別TTLは [`cache/cache_policy.yaml`](cache/cache_policy.yaml) にあります。freshはTTL内、staleは期限切れcacheを取得失敗/オフライン時にfallback利用、unavailableは有効cacheも取得値もない状態です。stale/unavailableの重要指標はSummaryにwarningを出し、fund scoring/recommendationは作りません。0、空値、nullは正常取得値として扱いません。

cacheは `cache/` にentity単位、metric/provider別で保存し、更新時に上書き可能です。月次snapshotは `data/market/YYYY-MM-DD/` にfund/index/macro/NISA別で保存し、原則上書きしません。同日の強制更新はrevisionを作ります。再生成はsnapshotを優先して当時の外部データ状態を再現します。外部値をGitHub PagesやGitへ誤って含めないため、`cache/`、`data/market/`、`data/private/` はGit ignore対象です。

cacheの個別削除例:

```powershell
Remove-Item -Recurse -Force cache/funds/VT.json
```

全cacheを消す場合は `Remove-Item -Recurse -Force cache`、月次snapshotは `data/market/` 内の対象日フォルダーを指定します。snapshotを消すとその月の外部データ再現性が失われ、offlineでは該当値がunavailableになります。

APIキーは `.env.example` を `.env` にコピーしてローカル設定します。`.env` はGit ignore対象です。キーをURL、source registry、コード、ログへ書かないでください。取得を有効にする前に、source/resource単位の利用規約、license、cache/publication権限、rate limit、request budgetを確認します。未確認なら取得せず、HTML scrapingもしません。raw responseは明示許可がない限り保存しません。Yahoo Finance adapterはlocal fixture専用で、sample値は実データと混ぜません。詳細は [External Data Policy](docs/external-data-policy.md) を参照してください。
APIキーは `.env.example` を `.env` にコピーしてローカル設定します。`.env` はGit ignore対象です。キーをURL、source registry、コード、ログへ書かないでください。取得を有効にする前に、source/resource単位の利用規約、license、cache/publication権限、rate limit、request budgetを確認します。未確認なら取得せず、HTML scrapingもしません。raw responseは明示許可がない限り保存しません。Yahoo Finance adapterはlocal fixture専用で、sample値は実データと混ぜません。詳細は [External Data Policy](docs/external-data-policy.md) を参照してください。

Alpha VantageとFREDはAPI仕様上keyをquery parameterで要求しますが、本プロジェクトのcredential policyはURLへのkey埋め込みを禁止します。そのため現状のadapterはfail-closedとなり、準拠する認証経路が用意されるまで利用できません。

## Branch Strategy

ブランチ名は `<type>/<short-description>` とし、英小文字・kebab-caseに統一します。typeは `feature`、`fix`、`data`、`refactor`、`docs`。月次データ更新は `data/2026-10` のように命名します。

## Data Sources

原CSVのファイル名、SHA-256、期間、推定出力時刻は `docs/data_sources.json` に登録します。公式ページのURL・参照日は設定/候補データに記録し、毎月自動的に参照日や費用率を更新しません。

## Assumptions and Methodology

- ページはExecutive Summary、Goal Tracker、現在保有、診断、NISA、候補ファンド、ポートフォリオ案、積立額、既存資産、リスク、Data & Methodologyの順に構成し、結論から詳細へ読み進められるようにします。
- 確認値、日付付き画面転記、モデル仮定を区別します。SBI NISA画面の残枠は入力値を正とし、保有残高や取得金額を生涯利用額として扱いません。
- 買付余力不足（買い付け余力不足）はレポートに掲載しません。既存資産の扱いは新規積立案と区別し、「新規積立を止める」と「既存保有を売却する」を混同しません。実際の売買注文を行ったとは記載しません。
- 金額は整数円、率はDecimalで計算して小数第2位に丸めます。保有明細だけを合算し、集計行を二重に加えません。取得金額は現在の保有分の簿価であり、累計入金額ではありません。
- 注文と約定は加算しません。未完了注文を購入済みとせず、取引IDのない対応付けを確定照合と表現しません。前回からの評価額増減を、そのまま運用リターンと呼ばないでください。
- 目標試算は収益予測ではありません。NISA満額月は対象商品をNISAで購入し、将来売却による枠再利用がない等の条件付きシナリオとして表示します。
- 全入出金履歴・期首評価額などが不足する場合、年率リターン、年初来リターン、最大ドローダウン、実現損益を作りません。概算の資産クラス比率と実際の組入比率を区別します。
- 公式資料のURLと参照日を記載します。参照日を自動的に当月へ変えず、再確認した場合だけ `scripts/diagnosis.py` の出典を更新します。スクリプトは資料の更新を自動取得しません。

## Directory Structure and Storage

```text
asset-management/
├─ README.md                 # Developer Docs・更新手順
├─ index.html                # 生成Dashboard（Pages root）
├─ config/
│  ├─ profile.json           # プロフィール
│  └─ goals.json             # 目標・積立シナリオ
├─ cache/                    # entity/metric/provider cache（Git ignore）
├─ data/
│  ├─ raw/csv/               # 保有CSVは複数時点、履歴は各種類1件
│  ├─ raw/screenshots/       # 補足画像（自動取込なし）
│  ├─ processed/             # 加工CSV・分析JSON
│  ├─ market/YYYY-MM-DD/     # 月次外部データsnapshot（Git ignore）
│  └─ history/               # スナップショット、originals/の保管原本
├─ reports/
│  ├─ index.html             # 月次レポート一覧
│  ├─ monthly/資産分析_yyyymm.html
│  └─ charts/資産配分_yyyymm.html
├─ scripts/                  # 取込・検証・集計・HTML生成・テスト
├─ providers/                # 外部データadapter・共通schema・指標計算
├─ sources.yaml              # provider候補・優先順位・規約/公開設定
└─ docs/
  ├─ handover.md            # 背景・現状・未確認事項
  ├─ analysis_assumptions.json # 現在設定・NISA画面転記
  ├─ fund_candidates.json   # 候補ファンド比較の入力
  ├─ external-data-policy.md # provider・license・publication policy
  └─ data_sources.json      # 元ファイル出典・SHA-256
```

元CSVは読み取り専用として扱います。加工CSVは `内容_開始YYYYMMDD_終了YYYYMMDD.csv`、分析JSON・スナップショットは出力日時を含む名前で保存します。HTMLの月次命名規則はこれらの内部データには適用しません。同じ入力時点の加工データは再生成で上書きするため、改訂履歴を永久保存する仕組みではありません。過去月のHTMLを保持しても、リンク先の加工データが同名で再生成される場合があります。
