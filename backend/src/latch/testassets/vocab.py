"""シードIntent生成の語彙プール(M4 ws-2・design §2.4)。

地名はci-db代表点の7地名(supervisor実査2026-10-03): 正転geocode_forwardは
normalized_name完全一致(_FORWARD_SQL・ORDER BY osm_poi優先,id)のため、
正転ヒットが確認できた地名のみを採用。座標はstaging投入時(POST /v1/intents)
にサーバの正転が返す代表点と同一値 — ws-3のE2EでもL1期待値が妥当になる。
7点ともsettings.py geo_osm_bbox=130.5420,31.5825,130.5740,31.6095 内。
カテゴリsecondary・soft文言はg2-jev-goldset(machine方式)の実績値から採用
(母集合参照・design §2.1案B・supervisor承認2026-10-03・本体YAML不変)。
文言はテスト資産のためログ制限の対象外(09 §4.1)。
"""

from __future__ import annotations

# 地名許可リスト(検証済み7地名。(lat, lon)はci-db代表点・osm_poi
# — supervisor実査2026-10-03・ws-2-report「固定値の変更有無」参照)
LOCATIONS: dict[str, tuple[float, float]] = {
    "天文館": (31.59095, 130.55543),
    "高見馬場": (31.58926, 130.55184),
    "呉服町": (31.59041, 130.55693),
    "加治屋町": (31.58544, 130.54828),
    "浜町": (31.60224, 130.56629),
    "城山": (31.60405, 130.54869),
    "鹿児島駅前": (31.60064, 130.56216),
}

# カテゴリ別secondary(g2実績値から採用)
CATEGORY_SECONDARY: dict[str, list[str]] = {
    "drinking": [
        "居酒屋",
        "バー",
        "立ち飲み",
        "ワイン",
        "ビール",
        "ハイボール",
        "日本酒",
        "焼酎",
        "バル",
        "おしゃれなバル",
    ],
    "meal": [
        "とんこつラーメン",
        "焼肉",
        "海鮮丼",
        "しゃぶしゃぶ",
        "イタリアン",
        "おむらいす",
        "定食",
        "カフェ",
        "甘味処",
        "ハンバーガー",
        "カレー",
        "寿司",
    ],
    "activity": ["カラオケ", "読書", "観光", "映画", "水族館", "街歩き", "ハイキング"],
}

# soft文言(g2実績値からカテゴリ別に採用)
SOFT_CONSTRAINTS: dict[str, list[str]] = {
    "drinking": [
        "のんびり席を温めたい",
        "ちょい飲みして帰りたい",
        "しっぽりと飲みたい",
        "軽く飲みたい",
        "まずは1杯だけ様子を見たい",
        "大人の雰囲気で",
        "しっとり静かな店がいい",
        "まったり語りたい",
        "締めの一杯",
        "銘酒をゆっくり味わいたい",
    ],
    "meal": [
        "がっつり食べたい",
        "軽めの食事で済ませたい",
        "さっと食べたい",
        "しっかり食べるつもり",
        "お腹を空かせてる",
        "野菜中心で",
        "肉系ならどこでもいい",
        "昼はサクッと",
        "昼は手早く",
        "早めに済ませたい",
    ],
    "activity": [
        "にぎやかに騒ぎたい",
        "わいわい盛り上がりたい",
        "静かにやりたい",
        "落ち着いた雰囲気で",
        "ゆっくり過ごしたい",
        "時間をかけて楽しみたい",
        "1時間くらいで切り上げたい",
        "サクッと帰るつもり",
        "短時間で済ませたい",
        "雰囲気良ければどこでも",
    ],
}

# raw_textテンプレート(カテゴリ別。{place}/{secondary}/{soft}を埋め込む)
RAW_TEMPLATES: dict[str, list[str]] = {
    "drinking": [
        "{time_prefix}{place}あたりで{secondary}に行きたい。{soft}",
        "{place}あたりで{secondary}を探してます。{soft}",
        "{time_prefix}{place}で{secondary}、一緒に行きませんか。{soft}",
    ],
    "meal": [
        "{time_prefix}{place}あたりで{secondary}を食べたい。{soft}",
        "{place}あたりで{secondary}に行きたいです。{soft}",
        "{time_prefix}{place}で{secondary}、同世代と一緒に。{soft}",
    ],
    "activity": [
        "{time_prefix}{place}あたりで{secondary}を楽しみたい。{soft}",
        "{place}あたりで{secondary}しませんか。{soft}",
        "{time_prefix}{place}で{secondary}、気軽に集まりたい。{soft}",
    ],
}

# 時間帯別のraw_text接頭辞
TIME_PREFIX: dict[str, str] = {
    "day": "昼に",
    "evening": "仕事終わりに",
    "night": "今夜",
    "other": "今度の休みに",
}

# 時間帯バケット(design §2.4の解釈: 開始時刻で分類。10 §2の分布=60/90/120/30)
# (name, 開始時刻の範囲[hour_from, hour_to)。"other"は範囲外判定)
TIME_BUCKETS: list[tuple[str, tuple[int, int] | None]] = [
    ("day", (12, 14)),  # 昼=12:00-13:59開始
    ("evening", (18, 20)),  # 夕=18:00-19:59開始
    ("night", (20, 23)),  # 夜=20:00-22:59開始
    ("other", None),  # その他=上記以外
]


def time_bucket_of(start_hour: int) -> str:
    """開始時刻→時間帯バケット(design §2.4の解釈。verifyも同一関数を使用)。"""
    for name, hours in TIME_BUCKETS:
        if hours is None:
            return name
        if hours[0] <= start_hour < hours[1]:
            return name
    raise AssertionError("unreachable")  # otherがNone到達を吸収する
