# How to CT - Django版

診療放射線技師向けCT検査マニュアルシステム（Django）

## 概要

このプロジェクトはStreamlitから**Django**に変更されました。
- シンプルで高速なウェブアプリケーション
- 疾患データ、お知らせ、CTプロトコルの管理機能
- 画像アップロード対応
- レスポンシブデザイン（Bootstrap）

## セットアップ手順

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. データベース初期化

```bash
python manage.py migrate
```

### 3. 開発サーバーの起動

```bash
python manage.py runserver
```

ブラウザで `http://localhost:8000` にアクセスしてください。

## プロジェクト構成

```
.
├── manage.py                    # Djangoマネジメントコマンド
├── requirements.txt             # Python依存パッケージ
├── medical_ct.db               # SQLiteデータベース
├── medical_ct_project/         # Django設定
│   ├── settings.py             # 設定ファイル
│   ├── urls.py                # URLルーティング
│   └── wsgi.py                # WSGI設定
├── ct_app/                     # メインアプリケーション
│   ├── models.py               # データベースモデル（Sick, Form, Protocol）
│   ├── views.py                # ビュー（ページ表示ロジック）
│   ├── urls.py                # URLパターン
│   ├── forms.py                # フォーム
│   ├── admin.py                # 管理画面設定
│   ├── templates/ct_app/       # HTMLテンプレート
│   │   ├── base.html           # 基本テンプレート
│   │   ├── index.html          # ホームページ
│   │   ├── sick_*.html         # 疾患関連テンプレート
│   │   ├── form_*.html         # お知らせ関連テンプレート
│   │   └── protocol_*.html     # プロトコル関連テンプレート
│   └── static/                 # 静的ファイル（CSS、JS）
└── media/                      # アップロード画像

```

## 主な機能

### 🔍 疾患検索
- 疾患名、キーワード、プロトコルで検索
- 疾患の詳細表示（あしあと：疾患情報、撮影プロトコル、造影プロトコル、画像処理）
- 疾患データの作成・編集・削除

### 📢 お知らせ
- お知らせの일覧表示
- お知らせの詳細表示と画像アップロード
- お知らせの作成・編集・削除

### 📋 CTプロトコル
- カテゴリー別プロトコル表示（頭部、頸部、胸部、腹部、下肢、上肢、特殊）
- プロトコルの詳細表示
- プロトコルの作成・編集・削除

## 使用技術

- **フレームワーク**: Django 4.2.0
- **データベース**: SQLite3
- **フロントエンド**: Bootstrap 5
- **画像処理**: Pillow

## Streamlit版からの主な変更点

| 項目 | Streamlit版 | Django版 |
|------|---------|----------|
| **フレームワーク** | Streamlit | Django |
| **セッション管理** | st.session_state | Django Sessions |
| **データベース操作** | SQLite（直接） | Django ORM |
| **UI構築** | Streamlit Widgets | HTML + Bootstrap |
| **ログイン機能** | あり | なし（簡簡化） |
| **API** | なし | REST API可能 |

## データベーススキーマ

### Sick（疾患）
- diesease（疾患名）
- diesease_text（疾患詳細）
- keyword（キーワード）
- protocol（撮影プロトコル）
- protocol_text（撮影詳細）
- processing（画像処理）
- processing_text（処理方法）
- contrast（造影プロトコル）
- contrast_text（造影詳細）
- 画像フィールド（疾患、プロトコル、処理、造影）

### Form（お知らせ）
- title（タイトル）
- main（本文）
- post_img（画像）

### Protocol（CTプロトコル）
- category（カテゴリー）
- title（プロトコルタイトル）
- content（内容）
- protocol_img（画像）

## トラブルシューティング

### ポート8000が既に使用されている場合

```bash
python manage.py runserver 8001
```

### データベースをリセットしたい場合

```bash
# medical_ct.dbファイルを削除
rm medical_ct.db

# マイグレーション実行
python manage.py migrate
```

### 静的ファイルが表示されない場合

```bash
python manage.py collectstatic
```

## ライセンス

このプロジェクトは内部用途のみです。

## サポート

問題が発生した場合は、GitHubのIssueを作成してください。