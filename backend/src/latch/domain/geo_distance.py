"""WGS84楕円体の測地線距離(T4・PostGIS geography互換)。

PostGISの ST_DWithin(geography) はWGS84回転楕円体上の測地線距離を
使う。ここではVincenty逆解法(収束0.5mm以下・境界判定の許容±1cmに
対して十分)を実装する。計画§3.3: 「PostGISのgeographyを単純な球面距離へ
交換すると境界判定が変わり得る。互換の距離計算を検証」— 決定的試験で
PostgreSQL実測と突き合わせる(test_retrieval_parity)。
"""

from __future__ import annotations

import math

# WGS84楕円体定数(EPSG 7030)
WGS84_A = 6378137.0  # 長半径[m]
WGS84_F = 1.0 / 298.257223563  # 扁平率
WGS84_B = WGS84_A * (1.0 - WGS84_F)  # 短半径[m]


def geodesic_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """2点の測地線距離[m](Vincenty逆解法・反復閾1e-12・最大200回)。

    近接・同一点は0を返す。反復非収束(ほぼ対蹠)は例外的に球面距離へ
    フォールバックする(境界判定用途に影響しない精度で完了させる)。
    """
    if lat1 == lat2 and lon1 == lon2:
        return 0.0
    a, b, f = WGS84_A, WGS84_B, WGS84_F
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    lam1, lam2 = math.radians(lon1), math.radians(lon2)
    delta_lam = lam2 - lam1

    u1 = math.atan((1.0 - f) * math.tan(phi1))
    u2 = math.atan((1.0 - f) * math.tan(phi2))
    sin_u1, cos_u1 = math.sin(u1), math.cos(u1)
    sin_u2, cos_u2 = math.sin(u2), math.cos(u2)

    lam = delta_lam
    for _ in range(200):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.sqrt(
            (cos_u2 * sin_lam) ** 2 + (cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lam) ** 2
        )
        if sin_sigma == 0.0:
            return 0.0  # 同一点
        cos_sigma = sin_u1 * sin_u2 + cos_u1 * cos_u2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cos_u1 * cos_u2 * sin_lam / sin_sigma
        cos_sq_alpha = 1.0 - sin_alpha * sin_alpha
        if cos_sq_alpha == 0.0:
            cos_2sigma_m = 0.0  # 両極間(赤道上)
        else:
            cos_2sigma_m = cos_sigma - 2.0 * sin_u1 * sin_u2 / cos_sq_alpha
        c = f / 16.0 * cos_sq_alpha * (4.0 + f * (4.0 - 3.0 * cos_sq_alpha))
        lam_prev = lam
        lam = l + (1.0 - c) * f * sin_alpha * (
            sigma
            + c
            * sin_sigma
            * (cos_2sigma_m + c * cos_sigma * (-1.0 + 2.0 * cos_2sigma_m**2))
        )
        if abs(lam - lam_prev) < 1e-12:
            break
    else:
        # 非収束(ほぼ対蹠) — 球面距離で代替(境界用途に十分)
        return _spherical_fallback(lon1, lat1, lon2, lat2)

    u_sq = cos_sq_alpha * (a * a - b * b) / (b * b)
    big_a = 1.0 + u_sq / 16384.0 * (
        4096.0 + u_sq * (-768.0 + u_sq * (320.0 - 175.0 * u_sq))
    )
    big_b = u_sq / 1024.0 * (256.0 + u_sq * (-128.0 + u_sq * (74.0 - 47.0 * u_sq)))
    delta_sigma = (
        big_b
        * sin_sigma
        * (
            cos_2sigma_m
            + big_b
            / 4.0
            * (
                cos_sigma * (-1.0 + 2.0 * cos_2sigma_m**2)
                - big_b
                / 6.0
                * cos_2sigma_m
                * (-3.0 + 4.0 * sin_sigma**2)
                * (-3.0 + 4.0 * cos_2sigma_m**2)
            )
        )
    )
    return b * big_a * (sigma - delta_sigma)


def _spherical_fallback(lon1, lat1, lon2, lat2) -> float:
    """球面距離(対蹠付近のVincenty非収束時の代替)。"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    h = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    )
    return 2.0 * WGS84_A * math.asin(math.sqrt(h))


def bbox_for_radius(lon: float, lat: float, radius_m: float) -> tuple:
    """距離事前絞り用の緯度経度bbox(余裕1%+50m — 境界を落とさない)。"""
    buf = radius_m * 1.01 + 50.0
    dlat = buf / 111_320.0
    cos_lat = max(abs(math.cos(math.radians(lat))), 1e-6)
    dlon = buf / (111_320.0 * cos_lat)
    return (
        lon - dlon,
        lat - dlat,
        lon + dlon,
        lat + dlat,
    )
