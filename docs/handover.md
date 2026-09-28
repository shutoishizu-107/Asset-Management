# 資産運用プロジェクト Handover

最終更新: 2026-09-27

## 次のセッションの入口

[README](../README.md) に月次作成の必要入力、出典情報の登録、実行方法、レポートの書き方を集約しています。運用手順はREADMEを正とし、この文書は背景・現在地・未確認事項を引き継ぎます。

- プロジェクト: `C:\Users\shuto\Downloads\asset-management`
- [Dashboard](../index.html)
- [レポート入口](../reports/index.html)
- [資産分析 2026年9月](../reports/monthly/資産分析_202609.html)
- [資産配分 2026年9月](../reports/charts/資産配分_202609.html)
- [出典情報](data_sources.json) / [積立前提](analysis_assumptions.json)

公開Dashboardはルート `index.html`、月次分析・配分チャートはHTMLを正本とします。分析名は `資産分析_yyyymm.html`、配分名は `資産配分_yyyymm.html`。READMEはDeveloper Docsとして運用手順を持ちます。元CSVと内部集計は保持します。

## 最新状態（2026-09-27）

### 完了済み

- iShares専用adapterを実装。公式product pageのJSON-LD Fund Factsと、JSON-LD `DataDownload.contentUrl`から発見したHoldings CSVを同じparserで処理します。
- 対象5銘柄: ACWI、ITOT、IXUS、EFA、IDEV。IDEVの公式product IDは`286762`です。
- iShares Facts/Holdingsで取得済み: `expense_ratio`、`aum`、`inception_date`、`fund_age`、`benchmark`、`number_of_holdings`、`holdings`。aggregate holdingsは拒否します。
- Tiingo price adapterを追加。`adjClose`、daily、USD、2016-01-01以降をresource templateから取得し、共通derived engineでreturn/volatility/max_drawdownを計算します。
- FRED TB3MSをruntime `.env` keyで取得し、annual percentからannual fractionへ正規化。price historyと組み合わせてSharpeを計算します。
- Scoring formula、weight、Role Universe、compact report layoutは変更していません。
- Reportは[資産分析 2026年9月](../reports/monthly/資産分析_202609.html)を再生成済みです。Provider Coverage、Coverage Breakdown、Fund Data Details、anchor構造を維持しています。

### Online E2E実績

- Tiingo ACWI: 2,698 observations、2016-01-04～2026-09-25、`adjusted_close` / `daily` / `USD`。
- Tiingo IDEV: 2,391 observations、2017-03-23～2026-09-25。設定日以降の利用可能期間を使用。
- FRED TB3MS: latest observation `2026-08-01`、annual fraction `0.0372`。source URLへkeyは含めません。
- ACWI derived: return_1y、return_3y_annualized、return_5y_annualized、volatility、max_drawdown、Sharpeがavailable。
- Price raw seriesはcache内部扱いとし、HTMLへ全件掲載しません。

### Cache・テスト

- Tiingoはunit testのnetwork混入を防ぐためregistryではdisabled-by-default。online E2E時だけ明示的runtime enablementを使用します。
- `.env`は`providers.registry.load_dotenv()`で読み込み、値はログ・URL・cacheへ出しません。`.env`は`.gitignore`対象、`.env.example`はplaceholderのみです。
- offline collectionではprice/FRED cache hitを使用し、request attemptは0です。
- 最新のfull unittest実測: `Ran 75 tests in 21.216s` / `OK`。network tripwire付きでもfailures=0、errors=0を確認済みです。

### 残課題

- Tiingo API key未設定環境ではonline price E2Eは`api_key_unavailable`になります。key設定後に明示的online refreshが必要です。
- benchmark_price_historyは未実装。benchmark名からproxyは推測しないため、tracking_difference/tracking_errorは別フェーズです。
- iShares coverageはprice追加後もRole候補・他metricの不足により低信頼です。Scoring formulaを変更せず、price/benchmark sourceの追加で改善します。
- FRED key未設定時のSharpeは計算不可ですが、risk-freeを0%へ置換しません。

## 旧セッション切替サマリ（2026-09-26時点・履歴）

以下は実装初期の状態記録です。現在の正本は上の「最新状態（2026-09-27）」です。

### 直近で確定した事実

- 外部データ取得が0件の主因はAPI障害ではなく、設定段階での未充足です。
- 実測上、`provider_count=21` に対して `enabled_provider_count=0`、`configured_resource_count=0`、`actual_force_refresh_requests=0` の状態でした。
- unavailable理由は大半が `resource_not_configured` で、現状は「取得処理まで到達していない」状態です。

### 直近で完了した修正（main 反映済み）

- 失敗理由の伝播改善:
  - 派生メトリクス（価格系）の失敗時に、依存元の失敗理由を引き継ぐよう修正。
- レポート表示改善:
  - `S&P` 表記の二重エスケープを解消。
- これらは `main` にコミット済み（`493dec6`）。

### 今の作業ブランチ

- ブランチ: `fix/external-data-e2e`
- 状態: 診断CLIとsubject×metric resource map実装まで完了。次はsource/derived分離の深化。

### 今回セッションで実施（2026-09-26）

- `scripts/diagnose_external_data.py` を追加
	- STAGE1..STAGE7（config/auth/network/response/normalize/derived/cache-snapshot）に分類
	- request試行数、cache hit/stale、available/unavailable、理由件数、derived依存失敗のroot causeをJSON出力
- `providers/collector.py` を拡張
	- 診断用のattemptログ・集計（provider_count/enabled/configured/request/force_refresh）を追加
	- provider設定に `resource_map` / `subject_metric_resources` を追加可能にし、subject×metric解決を実装
	- 後方互換として、既存 `resources` がある場合は従来定義を優先
- `providers/registry.py` の `collect_latest()` に `diagnostics` 出力と `metrics` 絞り込み引数を追加
- `sources.yaml` の `vanguard` にVT向け `resource_map` を追加（最小E2Eの解決経路確認用）
- 追加実施（同日更新）
	- `vanguard` providerを実運用設定で有効化（enabled/approved）
	- VT実取得URLを `https://investor.vanguard.com/irr/funds/profile/VT` に更新
	- 実HTTP取得を確認（`http_status=200`）
	- `cache/funds/VT.json` への保存と2回目 `cache_hit` を確認
	- source `expense_ratio` から derived `expense_ratio_bps` を生成
	- `DataRecord` に `dependency` / `root_cause` / `http_status` を追加し、診断CLIで可視化
- `README.md` に診断コマンド例を追記
- テスト
	- 追加: `scripts/test_external_diagnose.py`
	- 追加: subject×metric resource map回帰 (`scripts/test_provider_data.py`)
	- 実行: `python -B -m unittest discover -s scripts -p 'test_*.py' -v`（51件成功）

### 診断実行メモ（offline）

- `python -B scripts/diagnose_external_data.py --offline`
- 主な結果:
	- `provider_count=21`
	- `enabled_provider_count=0`
	- `configured_resource_count=1`（VTのresource map追加反映）
	- `request_attempt_count=0`
	- unavailable主因は引き続き `resource_not_configured`

### 診断実行メモ（online, VT最小E2E）

- 強制更新（VTのみ）: `python -B scripts/diagnose_external_data.py --refresh-external-data --metrics expense_ratio`
	- `enabled_provider_count=1`
	- `configured_resource_count=1`
	- `request_attempt_count=1`
	- `successful_fetch_logs` に `subject=VT`, `metric=expense_ratio`, `provider=vanguard`, `http_status=200`, `status=success`
- 通常実行（2回目, VTのみ）: `python -B scripts/diagnose_external_data.py --metrics expense_ratio`
	- `request_attempt_count=0`
	- `cache_hit_count=1`

### 次セッションの実装優先順

1. 診断コマンド追加（`scripts/diagnose_external_data.py`）
	- STAGE1..STAGE7の段階分類（config/auth/network/response/normalize/derived/cache-snapshot）
	- request試行数、成功/失敗、cache hit/stale、unavailable件数を集計
2. VT最小E2Eのsubject×metricリソース解決を追加
	- `sources.yaml` にVT向けの明示的resource mapを追加
	- providerの暗黙 `resources=[]` 依存をやめ、subject解決経由に統一
3. source/derived分離と依存失敗の根因保持
	- `source_metrics` / `derived_metrics` / dependency graph を導入
	- `dependency_failed` + `root_cause=*` を明示
4. 段階コード付き失敗理由へ統一
	- あいまいな `fetch failed` 表現を排除
5. VT最小E2E統合テストを追加
	- 正常系（configured→fetch→normalize→cache→derived→snapshot）
	- 異常系（disabled/key欠落/resource欠落/auth失敗/malformed/offline/force-refresh/stale fallback）

### 実装時の注意

- URL・設定・ログに秘密情報を埋め込まない既存ポリシーを維持。
- `.env` ローカル限定運用を維持。
- source（生データ）とderived（計算結果）をレコード上で混同しない。

## 現在地

保有・注文・約定CSVの取込と検証、口座・商品別集計、配分診断、積立案の比較試算、HTML生成コードを作成済みです。月次運用はREADMEの手順で継続できます。スクリプトは入力CSVをローカルで処理し、投資判断を実行しません。

現在の入力:

- `data/raw/csv/保有状況_20260926_20260926.csv`: 5口座区分、11明細、6ファンド。
- `data/raw/csv/積立買付注文履歴_20210927_20260926.csv`: 検索期間2021-09-27～2026-09-26、132件。
- `data/raw/csv/約定履歴_20240927_20260926.csv`: 検索期間2024-09-27～2026-09-26、53件。

保有CSVの推定出力日時は2026-09-26 15:19:45。価格の評価基準日時・タイムゾーンはCSV本文に記載がなく未確定です。保有状況はまだ1時点で、前回比較には次の同範囲の保有CSVが必要です。履歴CSVは各種類1ファイルを使用し、重複期間を自動結合しません。

確認値は投信評価額 **10,592,284円**、保有分の取得金額 **7,228,144円**、評価損益 **+3,364,140円（+46.54%）**。商品別比率はS&P500系48.04%、8資産均等型27.24%、オール・カントリー13.20%、FANG+11.07%、インド株0.45%です。

初回引継ぎの損益とは7円差があり、原因は未確認。CSVの明細・口座・全体集計は整合しています。銀行残高150,200円・総資産10,742,484円は2026-09-26の引継ぎ参考値で、主要集計には加算していません。

## 投資背景と提案の位置づけ

ユーザーは2003年1月生まれ（2026-09-26時点で23歳）で、35歳までに資産1億円を目指す意向です。長期投資・成長重視を前提とし、米国の長期的な競争力には比較的強気ですが、極端な集中は避けたい意向があります。新興国の独立した大きな配分には慎重で、オール・カントリー経由の保有を検討しています。既存資産を維持し、新規積立の変更で徐々に配分を調整する考え方を優先します。

引継ぎ上の現在の積立設定はS&P500、オール・カントリー、FANG+に各50,000円/月です。今回の年齢別積立目標をレポートの中心方針とし、以前の45,000円・90,000円・15,000円の暫定推奨は今後の月次レポートに掲載しません。A～D案はいずれも比較用で、未採用です。実際の設定変更は確認されていません。

本人申告の目標向け想定は23～25歳が月150,000円、26～29歳が月200,000円、30歳以降は月250,000円を検討中です。月200,000円継続案と比較し、25万円は確定した設定ではなくシナリオとして扱います。生年月は `config/profile.json`、目標額・年齢別積立・リターンシナリオは `config/goals.json` に保存しています。旧21件比較メモは `docs/old_fund_candidates.json` にアーカイブし、現在のレポートや推奨ロジックには使用しません。CSVにはジュニアNISAを含むため、現保有額のうち本人に帰属する範囲は未確認です。

候補となる配分A～Dはすべて新規積立額に対するシナリオで、既存保有を含む資産全体の比率ではありません。価格変化等を織り込まない積立試算を収益予測として扱わないでください。

## 未確認事項・継続時の注意

- 生活防衛資金、近い将来の支出、許容損失額、ジュニアNISAの名義・目的。個人と家族全体の資産を同一視しないこと。
- 実質米国比率とファンド間の重複率は未算出。同一基準日の組入資料が必要です。
- 全入出金履歴や期首評価額等がなく、年率・年初来リターン、最大ドローダウン、実現損益は未確定です。
- SBI NISA画面の2026-09-25転記値は `docs/analysis_assumptions.json` に保存済み。画面ファイルは未保存で、以降の売却による枠再利用、他金融機関、名義差は未確認です。以後は画面に表示された利用可能額を直接更新し、保有簿価・取引履歴から逆算しません。
- 公開公式資料は2026-09-26に参照済み。同じ分析目的での参照は許可済みです。出典と参照日を保持し、必要時に再確認します。
- 原本は変更・削除せず、口座情報・保有額・CSVを外部送信しません。実際の注文や積立設定の自動変更は行いません。
- ユーザーは読み込みに適宜sub agentを活用することを希望しています。
