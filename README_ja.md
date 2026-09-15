[English](README.md) | 日本語

# winshm-py

**winshm-py** は、東京大学地震研究所（ERI）が開発した WIN-System と互換性のある、Linux共有メモリ（IPC）用の Pure Python 読み書きライブラリです。

C言語の外部バイナリやサブプロセスを呼び出すことなく、Pythonから直接共有メモリセグメント上の WIN 形式波形データの読み出しおよび書き込み（注入）が行えます。

## 概要

WINシステムは、日本の地震観測網において長年標準的に利用されてきた多チャネル地震波形データ処理システムです。従来、共有メモリへのアクセスはC言語製ツール群が中心でしたが、`winshm-py` は Python ネイティブな実装を提供することで、超低遅延なIPCストリーム処理を実現します。

これにより、リアルタイムな WIN データストリームと現代の Python 向け AI/機械学習パイプライン（PyTorch, TensorFlow, PhaseNet, ObsPy など）とのシームレスな直接連携やテストベンチ構築が可能になります。

## 主な特徴

- **Pure Python 実装**: C言語バイナリ依存やラッパースクリプトなしで Linux 共有メモリ (IPC) と直接通信。
- **双方向の共有メモリ I/O**: 共有メモリからの読み込み (`win_shm_reader.py` / `pyshmin.py`) および書き込み (`win_shm_writer.py` / `pyshmout.py`) の両方に対応。
- **低遅延 IPC 処理**: リアルタイム波形ストリーミングや AI 検測モデルの評価環境に最適なメモリ直接アクセス。
- **Stock WIN との互換性**: 東京大学地震研究所（ERI）開発の標準 WIN システムの共有メモリ構造およびパケット形式に準拠。

## リポジトリ構成

- `win_shm_reader.py` / `pyshmin.py`: 共有メモリセグメントから WIN パケットを取得するリーダーコンポーネント。
- `win_shm_writer.py` / `pyshmout.py`: 共有メモリセグメントへ WIN パケットを注入するライターコンポーネント。
- `win_shm_common.py`: 共有メモリ構造体の定義および IPC 共通ユーティリティ。
- `win_packet.py`: WIN パケット構造の解析・パッキング関数。

## 動作環境

- **OS**: Linux / Ubuntu (System V 共有メモリ IPC 環境が必要)
- **Python**: 3.10 以上 (Python 3.12 推奨)

## 基本的な使い方

### 共有メモリからの読み込み
```python
from win_shm_reader import WinShmReader

# 共有メモリセグメントにアタッチ
reader = WinShmReader(shm_id=1)
for packet in reader.read_packets():
    print(packet)
```

### 共有メモリへの書き込み
```python
from win_shm_writer import WinShmWriter

# 共有メモリセグメントへパケットを書き込み
writer = WinShmWriter(shm_id=1)
writer.write_packet(raw_packet_bytes)
```

## ライセンス

本プロジェクトは **GNU General Public License v2.0 (GPL-2.0)** のもとで公開されています（東京大学地震研究所のオリジナル WIN-System のライセンスに準拠）。

## 参考文献・リンク

- 卜部卓・束田進也, 1992. win─微小地震観測網波形験測支援のためのワークステーション・プログラム（強化版）, 日本地震学会講演予稿集1992年度秋季大会，P 41.
- 卜部 卓，1994，多チャンネル地震波形データのための共通フォーマットの提案 , 日本地震学会講演予稿集 , No. 2, P24.
- ERI WINシステム マニュアル: [https://wwweic.eri.u-tokyo.ac.jp/WIN/man.ja/](https://wwweic.eri.u-tokyo.ac.jp/WIN/man.ja/)
