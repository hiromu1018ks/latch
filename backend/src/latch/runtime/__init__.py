"""runtime.cloudflare: Workers実行環境の組立(T5〜)。

業務計算は domain/ 既存計算を再利用し、この層はbindingとadapterの
接続のみを行う(計画§2.3: 専用の組立処理で必要なrouteとserviceを注入)。
"""
