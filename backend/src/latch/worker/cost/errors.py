"""コスト保護系例外(design §2.4-7。ratelimit/errors.pyと同型)。"""


class JevCostDependencyError(Exception):
    """Redis接続障害(fail-closed — コスト保護の失敗を実行の通り抜けにしない)。

    呼び出し側(ws-5のLayer 4直前・ws-4のreeval)は再試行経路へ載せる。
    """
