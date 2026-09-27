"""系統別プロバイダABC(design §2.2)。M0の実装はStubLLMのみ(T1確定後に実装が現れる)。

差し替え単位=系統(04 第4節 D-14基準6: 複数プロバイダのGateway経由併存)。
実装漏れを静かに防ぐABC(Clockと同じ判断。1モジュールに閉じる)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

EMBEDDING_DIMENSIONS = 768  # 05 第2節 vector(768)。C11: モデル確定までの固定値


class ParserProvider(ABC):
    """07 第2節 Intent Parser。送信内容=ユーザー入力テキスト(design §1.3)。"""

    name: str  # 送信記録の送信先(08 第3節)。実装クラスが設定する

    @abstractmethod
    async def complete_structured(self, text: str, current_date: date) -> dict: ...


class EmbeddingProvider(ABC):
    """07 第3節 Embedding。正規化テキスト(raw_text不使用)→768次元ベクトル。"""

    name: str

    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...


class JevProvider(ABC):
    """07 第4節 Jev。2 Intent分の正規化テキスト([hard]/[soft]タグ付き)→7設問JSON。"""

    name: str

    @abstractmethod
    async def judge(self, intent_a: str, intent_b: str) -> dict: ...
