"""ports: 実装を差し替える境界のProtocol群(T2)。

PostgreSQL版(SQLAlchemy)とWorkers版(D1)が同じ業務計算の上に乗るための
契約。Protocolのみ・実装を持たない。各実装はT3以降でadapters/に置く。
"""
