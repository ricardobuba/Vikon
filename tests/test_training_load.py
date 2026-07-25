"""Tests de TSS y de las series CTL/ATL/TSB."""

from __future__ import annotations

from datetime import date, timedelta

from cycling_coach.physiology.training_load import (
    compute_ctl_atl_tsb,
    hr_trimp_tss,
    training_stress_score,
)


def test_tss_one_hour_at_ftp_is_100():
    # 1 h exactamente al FTP (IF=1) → TSS = 100 por definición.
    assert abs(training_stress_score(np_w=300.0, duration_s=3600, ftp_w=300.0) - 100.0) < 1e-6


def test_tss_scales_with_intensity_squared():
    # IF=0.8 durante 1 h → TSS = 0.8² · 100 = 64.
    assert abs(training_stress_score(240.0, 3600, 300.0) - 64.0) < 1e-6


def test_tss_zero_without_power_or_ftp():
    assert training_stress_score(0.0, 3600, 300.0) == 0.0
    assert training_stress_score(300.0, 3600, 0.0) == 0.0


def test_hr_trimp_threshold_hour_is_about_100():
    # 1 h a ~umbral (HRr=0.85: HR=169 con rest=50, max=190) → TSS-equiv ≈ 100.
    tss = hr_trimp_tss(avg_hr=169.0, duration_s=3600, hr_rest=50.0, hr_max=190.0)
    assert 90.0 < tss < 110.0


def test_hr_trimp_zero_below_rest():
    assert hr_trimp_tss(avg_hr=45.0, duration_s=3600, hr_rest=50.0, hr_max=190.0) == 0.0


def test_hr_trimp_rises_with_intensity():
    easy = hr_trimp_tss(120.0, 3600, 50.0, 190.0)
    hard = hr_trimp_tss(175.0, 3600, 50.0, 190.0)
    assert hard > easy


def test_ctl_atl_converge_to_constant_load():
    # Carga diaria constante de 100 TSS → CTL y ATL convergen a 100.
    start = date(2024, 1, 1)
    daily = {start + timedelta(days=i): 100.0 for i in range(400)}
    series = compute_ctl_atl_tsb(daily)
    last = series[-1]
    assert abs(last.ctl - 100.0) < 1.0
    assert abs(last.atl - 100.0) < 1.0
    assert abs(last.tsb) < 1.0                 # en equilibrio, forma ~0


def test_atl_rises_faster_than_ctl():
    # Tras un bloque de carga, la fatiga (ATL, τ corto) supera al fitness (CTL).
    start = date(2024, 1, 1)
    daily = {start + timedelta(days=i): 120.0 for i in range(14)}
    series = compute_ctl_atl_tsb(daily)
    assert series[-1].atl > series[-1].ctl     # ATL sube más rápido
    assert series[-1].tsb < 0                   # forma negativa (fatigado)


def test_rest_days_filled_and_tsb_recovers():
    # Un bloque duro y luego descanso: la forma (TSB) se recupera (sube).
    start = date(2024, 1, 1)
    daily = {start + timedelta(days=i): 150.0 for i in range(10)}
    series = compute_ctl_atl_tsb(daily, ctl0=50.0, atl0=50.0)
    # Extendemos con descanso añadiendo un día final lejano con 0 carga.
    daily[start + timedelta(days=40)] = 0.0
    series2 = compute_ctl_atl_tsb(daily, ctl0=50.0, atl0=50.0)
    assert len(series2) == 41                    # días rellenados
    assert series2[-1].tsb > series[-1].tsb      # tras descansar, mejor forma
