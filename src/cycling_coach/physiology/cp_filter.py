"""Filtro de Kalman secuencial para CP(t) y W'(t) — diseño en dos etapas.

Etapa 1 (fuera del filtro): por cada ventana temporal se construye la curva de
potencia robusta (máximo real, excluyendo actividades anómalas) y se ajusta
`[CP, W']` por lotes con `fit_cp_wprime` (bien condicionado: usa todo el rango de
duraciones a la vez). Eso produce una OBSERVACIÓN directa del estado, con su
incertidumbre analítica.

Etapa 2 (este filtro): un Kalman con observación identidad (H = I) suaviza esas
observaciones en el tiempo. Al observar el estado directamente no hay
acoplamiento CP↔W' en la actualización, y un cambio real de forma se atribuye
limpiamente. El ruido de proceso hace que la incertidumbre crezca en los huecos
(lo viejo decae); las ventanas sin esfuerzo maximal simplemente no se observan.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from cycling_coach.domain.models import Estimate
from cycling_coach.metrics import mean_maximal_power
from cycling_coach.metrics.cleaning import clean_power
from cycling_coach.physiology.critical_power import CP_FIT_DURATIONS

_Z90 = 1.645


@dataclass
class CPFilterConfig:
    q_cp: float = 2.0            # ruido de proceso de CP (W²/día)
    q_wp: float = 2.0e5         # ruido de proceso de W' (J²/día)
    decay_tau_days: float = 0.0     # 0 = sin decaimiento; >0 = "úsalo o piérdelo"
    decay_floor_frac: float = 0.75  # suelo al que revierte sin datos
    # FTP (potencia ~60 min) ≈ ftp_ratio·CP. Con el método de prior sobre W'
    # (pesa esfuerzos largos), el CP ya sale cerca de la asíntota real, así que
    # FTP ≈ CP. El factor exacto se calibra con un test. Ver hilo de diseño.
    ftp_ratio: float = 0.99
    # Asimetría "úsalo o piérdelo": una observación por DEBAJO del CP actual se
    # cree menos (×down_weight su sd) — puede ser un día no-maximal, no pérdida de
    # forma. La forma sube rápido con un buen esfuerzo y baja despacio.
    down_weight: float = 6.0
    # Escala global del ruido de observación (aprendible). >1 = fiarse menos de
    # las observaciones; <1 = más. Se calibra maximizando la verosimilitud
    # predictiva (ver physiology/tune.py). 1.0 = usar la sd tal cual.
    obs_noise_scale: float = 1.0


@dataclass
class CPObservation:
    """Observación directa del estado (de un ajuste por lotes de una ventana)."""

    when: datetime
    cp: float
    w_prime: float
    sd_cp: float
    sd_wp: float


@dataclass
class CPState:
    as_of: datetime
    cp: Estimate
    w_prime: Estimate
    ftp_w: float
    updated: bool


class CriticalPowerFilter:
    """Kalman de estado [CP, W'] con observación identidad (H = I)."""

    def __init__(
        self,
        cp0: float,
        wp0: float,
        sd_cp0: float = 25.0,
        sd_wp0: float = 5000.0,
        config: CPFilterConfig | None = None,
    ) -> None:
        self.cfg = config or CPFilterConfig()
        self.x = np.array([cp0, wp0], dtype=float)
        self.P = np.diag([sd_cp0**2, sd_wp0**2]).astype(float)
        self._last: datetime | None = None

    def predict(self, dt_days: float) -> None:
        if dt_days <= 0:
            return
        if self.cfg.decay_tau_days > 0:
            k = 1.0 - float(np.exp(-dt_days / self.cfg.decay_tau_days))
            self.x = self.x + k * (self.x * self.cfg.decay_floor_frac - self.x)
        self.P = self.P + np.diag([self.cfg.q_cp * dt_days, self.cfg.q_wp * dt_days])

    def update(self, cp_obs: float, wp_obs: float, sd_cp: float, sd_wp: float) -> None:
        sd_cp *= self.cfg.obs_noise_scale
        sd_wp *= self.cfg.obs_noise_scale
        # Asimetría: si la observación de CP está por debajo del estado, se cree
        # menos (envolvente, no promedio).
        if cp_obs < self.x[0]:
            sd_cp = sd_cp * self.cfg.down_weight
        R = np.diag([sd_cp**2, sd_wp**2])
        S = self.P + R
        K = self.P @ np.linalg.inv(S)          # H = I
        z = np.array([cp_obs, wp_obs], dtype=float)
        self.x = self.x + K @ (z - self.x)
        self.P = (np.eye(2) - K) @ self.P

    def observe(self, obs: CPObservation) -> None:
        if self._last is not None:
            self.predict((obs.when - self._last).total_seconds() / 86400.0)
        self._last = obs.when
        self.update(obs.cp, obs.w_prime, obs.sd_cp, obs.sd_wp)

    def state(self, when: datetime, updated: bool = True) -> CPState:
        cp, wp = float(self.x[0]), float(self.x[1])
        sd_cp = float(np.sqrt(max(self.P[0, 0], 0.0)))
        sd_wp = float(np.sqrt(max(self.P[1, 1], 0.0)))
        return CPState(
            as_of=when,
            cp=Estimate(cp, sd_cp, (cp - _Z90 * sd_cp, cp + _Z90 * sd_cp), when, "learned"),
            w_prime=Estimate(wp, sd_wp, (wp - _Z90 * sd_wp, wp + _Z90 * sd_wp), when, "learned"),
            ftp_w=self.cfg.ftp_ratio * cp,
            updated=updated,
        )


# --------------------------------------------------------------------------- #
#  Etapa 1: construir observaciones (ventanas → ajuste por lotes)
# --------------------------------------------------------------------------- #
def _find_anomalous_keys(
    mmp_by_key: dict, fit_durations: Sequence[int], threshold: float = 1.25
) -> set:
    """Actividades cuyo récord supera `threshold`× el 2º mejor de OTRA actividad."""
    bad: set = set()
    for d in fit_durations:
        vals = sorted(
            ((k, m[d]) for k, m in mmp_by_key.items() if d in m),
            key=lambda kv: kv[1],
            reverse=True,
        )
        if len(vals) >= 2:
            best_key, best = vals[0]
            second = next((v for k, v in vals[1:] if k != best_key), None)
            if second and best > threshold * second:
                bad.add(best_key)
    return bad


def _true_max_curve(mmps: list[dict], fit_durations: Sequence[int]) -> dict[int, float]:
    curve: dict[int, float] = {}
    for m in mmps:
        for d in fit_durations:
            if d in m and m[d] > curve.get(d, 0.0):
                curve[d] = m[d]
    return curve


def _cp_from_curve_wprior(
    curve: dict[int, float],
    fit_durations: Sequence[int],
    wprime_mean: float,
    wprime_sd: float,
    meas_rel_sd: float,
) -> tuple[float, float] | None:
    """Estima CP de una curva usando un PRIOR sobre W'.

    Cada esfuerzo maximal a duración d implica CP_d = P(d) − W'/d. Con el prior
    de W', esto da una estimación de CP por duración, con varianza (W'_sd/d)² +
    ruido de medición. Los esfuerzos LARGOS tienen menor varianza → pesan más
    (están más cerca de la asíntota). Se combinan por varianza inversa.

    La CI final incorpora la DISPERSIÓN entre duraciones: si los esfuerzos (de
    días distintos, o no todos maximales) discrepan, la incertidumbre crece.
    Eso es lo que luego pedirá un test.
    """
    cps, weights = [], []
    for d in fit_durations:
        if d not in curve:
            continue
        p = curve[d]
        cp_d = p - wprime_mean / d
        var_d = (wprime_sd / d) ** 2 + (meas_rel_sd * p) ** 2
        cps.append(cp_d)
        weights.append(1.0 / var_d)
    if len(cps) < 2:
        return None
    cps_a = np.array(cps)
    w = np.array(weights)
    cp_obs = float(np.sum(cps_a * w) / np.sum(w))
    var_comb = 1.0 / float(np.sum(w))                       # precisión combinada
    spread = float(np.sum(w * (cps_a - cp_obs) ** 2) / np.sum(w))  # discrepancia
    sd_cp = float(np.sqrt(var_comb + spread))
    return cp_obs, sd_cp


def _wprime_from_curve(
    curve: dict[int, float],
    fit_durations: Sequence[int],
    cp: float,
    short_max_s: int = 420,
) -> tuple[float, float] | None:
    """Estima W' de los esfuerzos CORTOS (≤ short_max_s), donde domina lo
    anaeróbico: W'_d = (P(d) − CP)·d. Combina por mediana (robusta a la
    incoherencia entre duraciones) y la sd sale de la dispersión.

    Devuelve None si la ventana no tiene esfuerzos cortos por encima de CP
    (→ no informa sobre W', no se debe forzar una observación)."""
    works = [
        (curve[d] - cp) * d
        for d in fit_durations
        if d <= short_max_s and d in curve and curve[d] > cp
    ]
    if not works:
        return None
    wp = float(np.median(works))
    sd = float(np.std(works)) if len(works) > 1 else wp * 0.3
    return wp, max(sd, 2000.0)


def observation_from_activity(
    when: datetime,
    watts: list,
    fit_durations: Sequence[int] = CP_FIT_DURATIONS,
    wprime_mean: float = 20000.0,
    wprime_sd: float = 10000.0,
    meas_rel_sd: float = 0.02,
    sample_hz: float = 1.0,
    confidence_sd_cap: float = 8.0,
) -> CPObservation | None:
    """Observación de ALTA confianza desde una actividad marcada como test maximal.

    Usa la curva REAL de la actividad (no un valor a mano) y, al saber que fue
    maximal, acota la sd de CP (`confidence_sd_cap`) → ancla fuerte el filtro.
    """
    curve = mean_maximal_power(clean_power(watts, sample_hz), fit_durations)
    result = _cp_from_curve_wprior(curve, fit_durations, wprime_mean, wprime_sd, meas_rel_sd)
    if result is None:
        return None
    cp_obs, sd_cp = result
    sd_cp = min(sd_cp, confidence_sd_cap)
    wp_result = _wprime_from_curve(curve, fit_durations, cp_obs)
    if wp_result is not None:
        wp_obs, sd_wp = wp_result
    else:
        wp_obs, sd_wp = wprime_mean, wprime_sd * 100.0
    return CPObservation(
        when=when, cp=cp_obs, w_prime=wp_obs, sd_cp=max(sd_cp, 3.0), sd_wp=max(sd_wp, 2000.0)
    )


def build_cp_observations(
    activities: list[tuple[datetime, object, list]],
    window_days: int = 42,
    stride_days: int = 14,
    fit_durations: Sequence[int] = CP_FIT_DURATIONS,
    min_effort_frac: float = 0.90,
    wprime_mean: float = 20000.0,
    wprime_sd: float = 10000.0,
    meas_rel_sd: float = 0.02,
    sample_hz: float = 1.0,
) -> list[CPObservation]:
    """Actividades (fecha, clave, stream_watts) → observaciones de CP.

    Por ventana: curva de máximo real (sin anómalas) → CP vía prior de W'
    (esfuerzos largos pesan más; la CI refleja la coherencia entre duraciones).
    Solo emite si la ventana tiene un esfuerzo casi-maximal.
    """
    if not activities:
        return []

    mmp_by_key: dict = {}
    when_by_key: dict = {}
    for when, key, watts in activities:
        mmp_by_key[key] = mean_maximal_power(clean_power(watts, sample_hz), fit_durations)
        when_by_key[key] = when
    anomalous = _find_anomalous_keys(mmp_by_key, fit_durations)

    clean = sorted(
        ((when_by_key[k], k) for k in mmp_by_key if k not in anomalous),
        key=lambda x: x[0],
    )
    if not clean:
        return []
    ref_dur = max(d for d in fit_durations if d <= 1200)

    observations: list[CPObservation] = []
    rolling_best = 0.0
    start, end = clean[0][0], clean[-1][0]
    cursor = start
    while cursor <= end:
        w_end = cursor + timedelta(days=window_days)
        win = [k for (when, k) in clean if cursor <= when < w_end]
        cursor += timedelta(days=stride_days)
        if not win:
            continue
        curve = _true_max_curve([mmp_by_key[k] for k in win], fit_durations)
        best_ref = curve.get(ref_dur, 0.0)
        rolling_best = max(rolling_best, best_ref)
        if rolling_best > 0 and best_ref < min_effort_frac * rolling_best:
            continue
        result = _cp_from_curve_wprior(
            curve, fit_durations, wprime_mean, wprime_sd, meas_rel_sd
        )
        if result is None:
            continue
        cp_obs, sd_cp = result
        obs_when = max(when_by_key[k] for k in win)   # fecha real, no fin de ventana

        # W' de los esfuerzos cortos de la ventana (si los hay); si no, prior con
        # sd enorme → el filtro no actualiza W' con ruido.
        wp_result = _wprime_from_curve(curve, fit_durations, cp_obs)
        if wp_result is not None:
            wp_obs, sd_wp = wp_result
        else:
            wp_obs, sd_wp = wprime_mean, wprime_sd * 100.0

        observations.append(
            CPObservation(
                when=obs_when,
                cp=cp_obs,
                w_prime=wp_obs,
                sd_cp=max(sd_cp, 4.0),
                sd_wp=max(sd_wp, 2000.0),
            )
        )
    return observations


def run_cp_filter(
    observations: list[CPObservation],
    config: CPFilterConfig | None = None,
    sd_cp0: float = 30.0,
    sd_wp0: float = 6000.0,
) -> list[CPState]:
    """Corre el filtro hacia delante. Cada punto usa solo el pasado (útil para la
    estimación ACTUAL; las históricas van con lag → usar `run_cp_smoother`)."""
    if not observations:
        return []
    first = observations[0]
    filt = CriticalPowerFilter(
        cp0=first.cp, wp0=first.w_prime, sd_cp0=sd_cp0, sd_wp0=sd_wp0, config=config
    )
    trajectory: list[CPState] = []
    for obs in observations:
        filt.observe(obs)
        trajectory.append(filt.state(obs.when, updated=True))
    return trajectory


@dataclass
class TestRecommendation:
    recommended: bool
    reason: str
    sd_cp: float
    days_since_effort: int | None


def assess_test_need(
    current: CPState,
    last_effort: datetime | None,
    as_of: datetime,
    sd_threshold: float = 12.0,
    staleness_days: int = 42,
) -> TestRecommendation:
    """¿Conviene un test? Sí si la incertidumbre del CP es alta o si hace mucho
    que no hay un esfuerzo maximal. Es la traducción del principio 6: el sistema
    declara cuándo NO sabe y pide un test en vez de inventar un número."""
    reasons: list[str] = []
    stale = (as_of - last_effort).days if last_effort is not None else None
    if current.cp.sd > sd_threshold:
        reasons.append(f"incertidumbre alta (±{current.cp.sd:.0f} W)")
    if stale is None:
        reasons.append("sin esfuerzos maximales registrados")
    elif stale > staleness_days:
        reasons.append(f"{stale} días sin esfuerzo maximal")
    return TestRecommendation(
        recommended=bool(reasons),
        reason="; ".join(reasons) if reasons else "confianza suficiente",
        sd_cp=current.cp.sd,
        days_since_effort=stale,
    )


def run_cp_smoother(
    observations: list[CPObservation],
    config: CPFilterConfig | None = None,
    sd_cp0: float = 30.0,
    sd_wp0: float = 6000.0,
) -> list[CPState]:
    """Suavizador RTS: cada punto usa TODA la información (pasado + futuro).

    Elimina el lag del filtro hacia delante → trayectoria histórica correcta.
    Asume paso de proceso F = I (paseo aleatorio; sin decaimiento).
    """
    if not observations:
        return []
    cfg = config or CPFilterConfig()
    first = observations[0]
    x = np.array([first.cp, first.w_prime], dtype=float)
    P = np.diag([sd_cp0**2, sd_wp0**2]).astype(float)

    # --- pasada hacia delante, almacenando predicho y filtrado ---
    x_pred, P_pred, x_filt, P_filt = [], [], [], []
    last: datetime | None = None
    for obs in observations:
        dt = 0.0 if last is None else (obs.when - last).total_seconds() / 86400.0
        last = obs.when
        Pp = P + np.diag([cfg.q_cp * dt, cfg.q_wp * dt])   # F = I
        xp = x.copy()
        sd_cp = obs.sd_cp * cfg.down_weight if obs.cp < xp[0] else obs.sd_cp
        R = np.diag([sd_cp**2, obs.sd_wp**2])
        K = Pp @ np.linalg.inv(Pp + R)
        z = np.array([obs.cp, obs.w_prime], dtype=float)
        x = xp + K @ (z - xp)
        P = (np.eye(2) - K) @ Pp
        x_pred.append(xp)
        P_pred.append(Pp)
        x_filt.append(x.copy())
        P_filt.append(P.copy())

    # --- pasada hacia atrás (RTS) ---
    n = len(observations)
    xs = [None] * n
    Ps = [None] * n
    xs[-1], Ps[-1] = x_filt[-1], P_filt[-1]
    for k in range(n - 2, -1, -1):
        C = P_filt[k] @ np.linalg.inv(P_pred[k + 1])
        xs[k] = x_filt[k] + C @ (xs[k + 1] - x_pred[k + 1])
        Ps[k] = P_filt[k] + C @ (Ps[k + 1] - P_pred[k + 1]) @ C.T

    out: list[CPState] = []
    for k, obs in enumerate(observations):
        cp, wp = float(xs[k][0]), float(xs[k][1])
        sd_cp = float(np.sqrt(max(Ps[k][0, 0], 0.0)))
        sd_wp = float(np.sqrt(max(Ps[k][1, 1], 0.0)))
        out.append(
            CPState(
                as_of=obs.when,
                cp=Estimate(cp, sd_cp, (cp - _Z90 * sd_cp, cp + _Z90 * sd_cp), obs.when, "learned"),
                w_prime=Estimate(
                    wp, sd_wp, (wp - _Z90 * sd_wp, wp + _Z90 * sd_wp), obs.when, "learned"
                ),
                ftp_w=cfg.ftp_ratio * cp,
                updated=True,
            )
        )
    return out
