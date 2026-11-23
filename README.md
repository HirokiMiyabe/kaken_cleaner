# kaken_cleaner

KAKEN（科学研究費助成事業データベース）の XML 書き出しを、1行＝1課題の JSON Lines に変換する道具です。
クラス `KakenToTable`（`Kaken_Cleaner_fast.py`）が本体です。

## 入れ方

```powershell
cd C:\Users\hiroj\MyProject\kaken_cleaner
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[test]"
```

`-e` で入れると、このフォルダのファイルを直すとそのまま反映されます。

## 使い方

```python
from Kaken_Cleaner_fast import KakenToTable

cleaner = KakenToTable("KAKEN_2000_2005.xml")
cleaner.to_jsonlines("kaken_refined/KAKEN_2000_2005.jsonl")
print(cleaner.seen, cleaner.written, cleaner.failed)
```

- `.xml.gz` はファイル名のままでは読めません。`gzip.open` で開いて渡してください。

```python
import gzip
with gzip.open("KAKEN_2000_2005.xml.gz", "rb") as source:
    KakenToTable(source).to_jsonlines("kaken_refined/KAKEN_2000_2005.jsonl")
```

- 保存先 `save_path` は必須です（v0.1.0 までは省略すると修論の `config.py` の `DATA` を参照していました）。保存先のフォルダは先に作ってください。
- 変換できなかった課題は `<保存先のフォルダ>/errors/<ファイル名>.errors.tsv` に記録し、処理を続けます。`error_path` で置き場所を変えられます。

## 出力の列

- **既存の列（v0.1.0 の列）の名前と値は変えません。** 修論などの分析がこれに依存しているためです。列を足すときは既存の列のあとに足し、ここに書きます。
- 分野の列（`field_nii`、`field_list`、`saimoku_list`、`saimoku_nii`、`saimoku_nii_all`、`n_saimoku`、`multi_saimoku`、`shumoku_nii`、`shumoku_path`、`review_section_list`、`summary_lang`）は、既存の列のあとにあります。nii は先頭の0を除いた文字列です（数字でない値はそのまま）。
- 段の名前（細目・分科・部）は付けません。段の判定は、nii をキーに分類表と結合して分析の側で行います。理由は `Kaken_Cleaner_fast.py` の冒頭の説明にあります。

## 版

| 版 | 中身 |
|---|---|
| v0.1.0 | 修論の版を改修したもの（nii の列つき）。試験6件 |
| 0.2.0.dev0（作業中） | 分野の要素が属性のない文字列でも課題を落とさない。保存先の既定値（修論の `config.py` への依存）を外す。`pip install -e` で入る形にする |

## フォルダ

- `reference/`：参考として残すだけのファイル（別実装の (2) など）。import も試験もしない

## 試験

```powershell
python -m pytest
```

試験は合成した XML で行います。**KAKEN の書き出しそのものはリポジトリに入れません**（`.gitignore` で `*.xml` と `*.jsonl` を除外しています）。

版を変えたときは、前の版と既存の列が一致することを確かめます。

```powershell
python tools/compare_versions.py v0.1.0:Kaken_Cleaner_fast.py Kaken_Cleaner_fast.py --synthetic 5000
python tools/compare_versions.py v0.1.0:Kaken_Cleaner_fast.py Kaken_Cleaner_fast.py --xml 手元のKAKEN_a.xml.gz 手元のKAKEN_b.xml.gz
```

## 動作を確かめる範囲

GitHub Actions（`.github/workflows/tests.yml`）で、Python 3.10〜3.13 と xmltodict 0.13.0・1.0.4 のすべての組み合わせについて、試験と v0.1.0 との比較を回します。xmltodict の版を変えても同じ XML から同じ出力になるかも確かめます。通った範囲を `pyproject.toml` の下限にします。

修論の環境（2026-11-23 に `pip list` で確認）は lxml 6.0.2・xmltodict 1.0.4 です。`kaken_refined` を作った時点の版と同じかどうかは、記録がないので分かりません。

## まだ決めていないこと

- パッケージ名（`pyproject.toml` では仮に `kaken-cleaner`。import 名は修論との互換のため `Kaken_Cleaner_fast` のまま）
- 対応する Python の版（いまは 3.10 以上。CI で 3.10〜3.13 の試験が通ることを確かめた）
- リポジトリの公開・非公開、ライセンス
- 版の付け方
