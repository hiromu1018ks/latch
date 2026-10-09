"""バンドル済みテストJWKS(backend/src/latch/auth/testkeys/idp_test_jwks.json対)。

WorkersのPyodide実行環境ではdata fileをPathで読めないため、同一内容を
Python定数へ埋める。load_jwks の差し替え(state.py)から使う。
"""

from __future__ import annotations

EMBEDDED_JWKS: dict = {
    "keys": [
        {
            "kty": "RSA",
            "kid": "test-idp-1",
            "use": "sig",
            "alg": "RS256",
            "n": "mzc7pAQ_pAVaskjR2m9lRRKP92jb_IqlL7zHPZ-hAQlr3T1hcjhP67F2XUVlRxv9DskZ-Mm5iwknkCUppF4AeFTvYk2SP5o15Rdn3cIvaSZfrV226SzJkJaBpKXNXPe4KvA5Myug3VUfLeNO_7Y8fGWoMy39Kl2oiAA6B6I97L3lGuDYtXfj30CwD0skMDP6ObVAM2ahom1NFVcwYkRzjoIf7xboy3mpVVJAkkmk_o3_pixHKo2hqpQ0e3thTNSR_AjhzZsgrfWZR46psa7xJzm8w8MQtTBzhcKmaLAb-HV1rikKnygK7kS5LJ5xcnpOlC-TESEnBht8z1DvBL5Hxw",
            "e": "AQAB",
        }
    ]
}
