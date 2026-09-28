"""レート制限(M1 ws-4)。08 §5.4の上限4種をRedisカウンタとDB計上で強制する。

横断関心事のためintentsドメインの外に置く(design §2.1-A)。StoreにRedis操作を
閉じ込め・limiterが判断する(auth/のSessionStoreと対称な構成)。
"""
