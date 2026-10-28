# Image Auth (電子透かし検出・耐性検証ツール)

周波数領域（DCT: 離散コサイン変換）を利用した、画像の耐性型電子透かし（Watermarking）ライブラリ・CLIツールです。
画質を損なうことなく画像に識別IDを埋め込み、JPEG圧縮・リサイズ・回転・クロップなどの加工処理を受けた後でも高い精度で透かしを検出できます。


## 目次

1. [主な特徴](#主な特徴)
2. [動作環境・依存ライブラリ](#動作環境依存ライブラリ)
3. [セットアップ](#セットアップ)
4. [使い方・コマンド詳細](#使い方コマンド詳細)
   - [1. 透かしの埋め込み (`embed`)](#1-透かしの埋め込み-embed)
   - [2. 透かしの検出 (`detect`)](#2-透かしの検出-detect)
   - [3. 耐性ベンチマークテスト (`benchmark`)](#3-耐性ベンチマークテスト-benchmark)
5. [オプション引数一覧](#オプション引数一覧)
6. [ディレクトリ構造](#ディレクトリ構造)

---

## 主な特徴

- **強力な加工耐性**: JPEG高圧縮（Quality 25等）、拡大・縮小、回転（±3°）、クロップ（最大20%切り抜き）に対しても透かしを検出可能。
- **自動位置合わせ（Alignment）**: クロップ時のキャンバス再配置探索やピクセルシフト補正を自動で行い、位置ズレを復元。
- **エラー訂正機能**: ECC（パリティチェック）およびハミング距離判定を導入し、ノイズに強い抽出を実現。
- **ベンチマークモード搭載**: 単一コマンドで各種画像加工に対する検出耐性テストを一括実行。

---

## 動作環境・依存ライブラリ

- **Python**: 3.8 以上
- **OpenCV** (`opencv-python`)
- **NumPy** (`numpy`)

---

## セットアップ

必要な外部ライブラリを事前にインストールします。

```bash
pip install opencv-python numpy

```

---

## 使い方・コマンド詳細

`image_auth.py` は、末尾に指定するサブコマンド（`embed` / `detect` / `benchmark`）によって異なる動作を行います。

```bash
python image_auth.py <サブコマンド> [オプション...] <画像パス>

```

---

### 1. 透かしの埋め込み (`embed`)

画像に秘密鍵と識別IDを使って電子透かしを埋め込みます。

#### 構文

```bash
python image_auth.py embed <画像パス> --id <識別ID> --key <暗号鍵> [--strength <強度>] [--repeat <重複回数>]

```

#### 引数説明

* `<画像パス>` *(必須)*: 透かしを埋め込みたい元画像（PNG, JPG, JPEGなど）のパス。
* `--id` *(必須)*: 画像に埋め込む識別データ（文字列）。例: `"001"`, `"user_1234"`
* `--key` *(必須)*: 透かし位置のランダム化・暗号化に使用する秘密鍵。
* `--strength` *(任意)*: 埋め込み強度（デフォルト: `35.0`）。数値を上げると加工に強くなりますが、画質に微小な影響が出ます。
* `--repeat` *(任意)*: 1ビットあたりの重複埋め込み回数（デフォルト: `5`）。

#### 実行例

```bash
python image_auth.py embed "C:\path\to\image.jpg" --id "001" --key "my_secret_key"

```

#### 出力

`image_auth.py` と同じ階層にある `image_auth_output/` フォルダに、透かし入り画像（`<元画像名>_auth.png`）が生成されます。

---

### 2. 透かしの検出 (`detect`)

画像から埋め込まれた透かし（識別ID）を抽出・検証します。

#### 構文

```bash
python image_auth.py detect <画像パス> --key <暗号鍵> [--expected-id <期待するID>] [--orig-size <幅> <高さ>]

```

#### 引数説明

* `<画像パス>` *(必須)*: 検証したい画像のパス（編集・加工済みの画像も可）。
* `--key` *(必須)*: 埋め込み時に使用した秘密鍵。
* `--expected-id` *(任意)*: 期待するIDを指定すると、抽出したIDとの一致判定（`id_match: true/false`）を行えます。
* `--orig-size` *(任意)*: リサイズやクロップ前の元画像サイズ（幅 高さ）を指定します。例: `--orig-size 1920 1080`

#### 実行例

```bash
python image_auth.py detect "C:\path\to\image_auth_output\image_auth.png" --key "my_secret_key" --expected-id "001"

```

#### 出力（JSON形式）

```json
{
  "status": "detected",
  "id": "001",
  "expected_id": "001",
  "id_match": true,
  "confidence": 100.0,
  "bit_confidence": 1.0,
  "file": "C:\\path\\to\\image_auth.png"
}

```

---

### 3. 耐性ベンチマークテスト (`benchmark`)

透かしの埋め込み、12パターンの加工処理の自動生成、そして各加工画像に対する透かし検出の一連の流れを一括テストします。

#### 構文

```bash
python image_auth.py benchmark <画像パス> --key <暗号鍵> --id <識別ID> [--strength <強度>]

```

#### 引数説明

* `<画像パス>` *(必須)*: テスト対象の元画像パス。
* `--key` *(必須)*: テストで使用する秘密鍵。
* `--id` *(普及/必須)*: テストで埋め込む識別ID。
* `--strength` *(任意)*: 埋め込み強度（デフォルト: `35.0`）。

#### 実行例

```bash
python image_auth.py benchmark "C:\Users\haru\Desktop\program\testhtml\tmsb.jpeg" --key "tmsb0306" --id "001"

```

#### 検証されるテスト項目一覧

1. **01_jpeg95.jpg**: JPEG保存（品質 95）
2. **02_jpeg75.jpg**: JPEG保存（品質 75）
3. **03_jpeg50.jpg**: JPEG保存（品質 50）
4. **04_jpeg25.jpg**: JPEG高圧縮（品質 25）
5. **05_resize80.jpg**: 80%に縮小
6. **06_resize50.jpg**: 50%に縮小
7. **07_brightness.jpg**: 明るさ調整（+20）
8. **08_contrast.jpg**: コントラスト変更（1.2倍）
9. **09_blur.jpg**: ガウシアンぼかし
10. **10_rotate2.jpg**: 2度回転
11. **11_crop10.jpg**: 10%中央切り抜き後・拡大
12. **12_crop20.jpg**: 20%中央切り抜き後・拡大

#### 出力

* 生成フォルダ: `./image_auth_output/benchmark/`
* 加工テスト用画像全12種 + 元の透かし画像（`00_watermarked.png`）
* ベンチマーク結果のCSVレポート（`benchmark_report.csv`）



---

## オプション引数一覧

| コマンド | 引数名 | 必須/任意 | 初期値 | 概要 |
| --- | --- | --- | --- | --- |
| **共通** | `--key` | **必須** | - | 透かしの暗号化・復元に使用する秘密鍵 |
| `embed` / `benchmark` | `--id` | **必須** | - | 画像に埋め込む識別ID文字列 |
| `embed` / `benchmark` | `--strength` | 任意 | `35.0` | 透かしの強さ（高いほど加工耐性向上、画質トレードオフ） |
| `embed` | `--repeat` | 任意 | `5` | 1ビットあたりの重複埋め込み回数 |
| `detect` | `--expected-id` | 任意 | `None` | 検証用の一致確認対象ID |
| `detect` | `--orig-size` | 任意 | `None` | 元画像の解像度（幅 高さ） |

---

## ディレクトリ構造

```text
image_auth/
├── image_auth.py              # メイン処理スクリプト
├── README.md                  # 本ドキュメント
└── image_auth_output/         # 実行結果出力フォルダ（自動生成）
    ├── <画像名>_auth.png       # embed コマンドの出力画像
    └── benchmark/             # benchmark コマンドの出力フォルダ
        ├── 00_watermarked.png
        ├── 01_jpeg95.jpg
        ├── ...
        ├── 12_crop20.jpg
        └── benchmark_report.csv # 検出結果のCSVサマリー

```

```

<ElicitationsGroup message="READMEの追加カスタマイズや機能追加のご要望があれば選択してください：">
  <Elicitation label="Web API（FastAPIなど）化のコードを作成する" query="image_auth.pyをWeb API（FastAPI）化するためのコードを作成してください。"/>
  <Elicitation label="GUI（Tkinter/PyQt）のインターフェースを作成する" query="image_auth.pyを操作するためのGUIデスクトップアプリのコードを作成してください。"/>
</ElicitationsGroup>

```