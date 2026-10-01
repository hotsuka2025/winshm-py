[English](README.md) | 日本語

# winshm-py

**winshm-py** は、東京大学地震研究所（ERI）が開発した [WINシステム](https://wwweic.eri.u-tokyo.ac.jp/WIN/Jindex.html) (卜部・束田 1992)と互換性のある、Linux共有メモリ（IPC）用の Python ネイティブな読み書きライブラリです。

C言語の外部バイナリやサブプロセスを呼び出すことなく、Pythonから直接共有メモリセグメント上の WIN 形式波形データの読み出しおよび書き込みが行えます。

## 概要

WINシステムは、日本の地震観測網において長年標準的に利用されてきた多チャネル地震波形データ処理システムです。従来、共有メモリへのアクセスはC言語製ツール群が中心でしたが、`winshm-py` は Python ネイティブな実装を提供することで、低遅延なIPCストリーム処理を実現します。

本プロジェクトの主目的は、**PythonからWIN共有メモリへアクセスすること**です。共有メモリの読み書き機能がライブラリの中心的な機能です。WINシステム全体のPythonによる置き換えを目指しているものではありません。Pythonを用いたリアルタイム処理との親和性向上や、学習目的でWINシステムを利用する際のハードルを下げる事を期待しています。

また実験用の補助機能として、WINブロックを MQTT 経由で送受信するツールと、共有メモリ上のWINブロックをファイルへ保存するツールを用意しています。これらはライブラリの本体ではなく、コア機能を使ったデータ通信や保存経路を試すための**実験用ユーティリティ**という位置づけです。MQTT通信機について、本家WINシステムの raw2mq/mq2raw との互換性は考慮していません。

## 主な特徴

- **Pure Python 実装**: C言語バイナリ依存やラッパースクリプトなしで Linux 共有メモリ (IPC) と直接通信。
- **双方向の共有メモリ I/O**: 共有メモリからの読み込み (`win_shm_reader.py` / `pyshmin.py`) および書き込み (`win_shm_writer.py` / `pyshmout.py`) の両方に対応。
- **低遅延 IPC 処理**: リアルタイム波形ストリーミングや AI 検測モデルの評価環境などで利用可能。
- **本家 WIN との互換性**: WINシステムの共有メモリ構造およびパケット形式に対応。
- **実験用 MQTT 通信**: `win_shm_mqtt_pub.py` / `win_shm_mqtt_sub.py` により、MQTT ブローカーを介して共有メモリ間でWINブロックを転送できます。
- **実験用ファイル保存**: `win_shm_recorder.py` により、共有メモリ上のWINブロックを分単位のWINファイルとして保存できます。

## リポジトリ構成

### 共有メモリのコア機能

- `win_shm_reader.py` / `pyshmin.py`: 共有メモリセグメントから WIN パケットを取得するリーダーコンポーネント。
- `win_shm_writer.py` / `pyshmout.py`: 共有メモリセグメントへ WIN パケットを注入するライターコンポーネント。
- `win_shm_common.py`: 共有メモリ構造体の定義および IPC 共通ユーティリティ。
- `win_packet.py`: WIN パケット構造の解析・パッキング関数。

### 実験用ユーティリティ

- `win_shm_mqtt_pub.py`: 共有メモリからWINブロックを読み出し、MQTTトピックへ送信します。
- `win_shm_mqtt_sub.py`: MQTTトピックを購読し、受信したWINブロックを共有メモリへ書き込みます。
- `win_shm_recorder.py`: 共有メモリからWINブロックを読み出し、分単位のWINファイルとして保存します。
- `win_file.py`: レコーダーが使用するWINファイル書き込みユーティリティ。

MQTT通信とファイル保存のユーティリティは、共有メモリのコア実装とは分離しています。主に実験、動作確認、データ経路の例示を目的としています。

## 動作環境

- **OS**: Linux / Ubuntu (System V 共有メモリ IPC 環境が必要)
- **Python**: 3.10 以上 (Python 3.12 でテスト)
- **MQTT ユーティリティ**: MQTT機能を使用する場合は `paho-mqtt` が必要。
- **MQTT ブローカー**: MQTT実験には Mosquitto などの MQTT ブローカーが必要。

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

### コマンドラインツールの使用例

#### `pyshmin` — WIN テキストフォーマットから共有メモリへ

`pyshmin` は、標準入力から受け取った **WIN テキストフォーマット**を WIN ブロックに変換し、共有メモリへ書き込みます。

例えば、WIN テキストフォーマットのデータをパイプで渡して使用できます。

```bash
cat waveform.txt | python pyshmin.py --shm-key 15
```

WIN テキストフォーマットは、時刻情報とチャンネルごとのサンプルデータから構成されます。

```text
2026 09 28 12 00 00 2
0101 100 0.123 0.125 0.121 ...
0102 100 0.456 0.452 0.459 ...
```

共有メモリを新規作成する場合は、サイズを指定できます。

```bash
cat waveform.txt | python pyshmin.py --shm-key 15 --shm-size 1048576
```

#### `pyshmout` — 共有メモリから WIN データを読み出す

`pyshmout` は、共有メモリに格納された WIN データを読み出して表示するコマンドラインツールです。

例えば、特定のチャンネルだけを表示できます。

```bash
python pyshmout.py --shm-key 15 -c 0101,0102,0103
```

WIN データをテキスト形式で出力することもできます。

```bash
python pyshmout.py --shm-key 15 -c 0101 -t
```

また、`--plot` オプションを使用すると、最大3チャンネルの波形をターミナル上にリアルタイム表示できます。

```bash
python pyshmout.py --shm-key 15 --plot 0101,0102,0103
```

`--plot` モードでは、波形表示に使うデシメーション方法を `last`、`mean`、`rms`、`p2p` から選べます。共有メモリへのデータ入力が正常に行われているかを、ターミナル上でリアルタイムに確認する用途に利用できます。

```bash
python pyshmout.py --shm-key 15 --plot 0101 --plot-metric rms
```


## 実験用ユーティリティ

### MQTT：共有メモリ間のデータ転送

MQTTユーティリティでは、WINブロックを共有メモリから取り出し、MQTTを経由して別の共有メモリへ転送する実験的な経路を構成できます。

```
[WINデータ供給元]
      |
      v
System-V共有メモリ
      |
      v
win_shm_mqtt_pub.py
      |
      | MQTT
      v
MQTTブローカー
      |
      v
win_shm_mqtt_sub.py
      |
      v
System-V共有メモリ
      |
      v
[WINデータ利用側]
```

Publisher の例:

```bash
python win_shm_mqtt_pub.py \
    --shm-key 15 \
    --broker 192.168.1.100 \
    --port 1883 \
    --topic win/15/raw
```

Subscriber の例:

```bash
python win_shm_mqtt_sub.py \
    --shm-key 16 \
    --shm-size 1024 \
    --broker 192.168.1.100 \
    --port 1883 \
    --topic win/15/raw
```

Publisher は生のWINブロックをMQTT payloadとして送信し、Subscriber は受信したpayloadを転送先のWIN共有メモリへ直接書き込みます。

### ファイル保存

`win_shm_recorder.py` は、共有メモリ上のWINブロックを分単位のWINファイルへ保存するための、実験用の簡単なレコーダーです。

```bash
python win_shm_recorder.py \
    --shm-key 15 \
    --output-dir ./win-data
```

デフォルトでは短いポーリング間隔で共有メモリを監視し、読み出しが遅れてリングバッファ上の現在位置から取り残された場合には、現在の書き込み位置までスキップします。この動作を変更したい場合は `--no-drop-if-behind` を指定します。

これらのユーティリティは、WIN共有メモリを中心とした通信経路・保存経路の実験や動作確認を目的としています。共有メモリの読み書きという本来の機能を置き換えるものではありません。

## ライセンス

本プロジェクトは **GNU General Public License v2.0 (GPL-2.0)** のもとで公開されています（東京大学地震研究所のオリジナル WIN-System のライセンスに準拠）。

## 参考文献・リンク

- ERI WINシステムHP: [https://wwweic.eri.u-tokyo.ac.jp/WIN/Jindex.html](https://wwweic.eri.u-tokyo.ac.jp/WIN/Jindex.html)
- ERI WINシステム マニュアル: [https://wwweic.eri.u-tokyo.ac.jp/WIN/man.ja/](https://wwweic.eri.u-tokyo.ac.jp/WIN/man.ja/)
- 卜部卓・束田進也, 1992. win─微小地震観測網波形験測支援のためのワークステーション・プログラム（強化版）, 日本地震学会講演予稿集1992年度秋季大会，P 41.
- 卜部 卓，1994，多チャンネル地震波形データのための共通フォーマットの提案 , 日本地震学会講演予稿集 , No. 2, P24.
