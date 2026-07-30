"""Estimación de CP/W'/FTP actuales fusionando actividades + tests de campo.

Los tests introducidos por el usuario entran como observaciones de ALTA
confianza (sd pequeña) que anclan el filtro. Lógica compartida por los comandos
`estimate-cp` y `add-test`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from sqlalchemy.orm import Session

from cycling_coach.db.repositories import (
    load_marked_test_activities,
    load_model_config,
    load_power_mmp,
    load_test_results,
    save_model_config,
)
from cycling_coach.physiology import (
    BacktestResult,
    CPFilterConfig,
    CPObservation,
    CPState,
    TestRecommendation,
    assess_test_need,
    backtest_one_step,
    build_cp_observations_from_mmp,
    learn_hyperparameters,
    observation_from_activity,
    run_cp_filter,
)

_WPRIME_NOMINAL = 20000.0
_SD_WP_IGNORE = 1.0e6      # sd enorme → el filtro no actualiza W' con ese punto


@dataclass
class CPEstimationResult:
    state: CPState
    recommendation: TestRecommendation
    n_activity_obs: int
    n_test_obs: int          # tests manuales (add-test) + actividades marcadas (mark-test)
    predictive_sd_cp: float  # incertidumbre HONESTA del CP actual (error demostrado)


def _config_from_dict(data: dict) -> CPFilterConfig:
    """CPFilterConfig desde un dict, ignorando claves desconocidas (drift)."""
    known = {f.name for f in fields(CPFilterConfig)}
    return CPFilterConfig(**{k: v for k, v in data.items() if k in known})


def resolve_config(
    session: Session, athlete_id: int, override: CPFilterConfig | None = None
) -> CPFilterConfig:
    """Config del filtro: la explícita, o la aprendida (model_config), o defaults."""
    if override is not None:
        return override
    stored = load_model_config(session, athlete_id)
    return _config_from_dict(stored) if stored else CPFilterConfig()


def _test_observations(session: Session, athlete_id: int) -> list[CPObservation]:
    obs: list[CPObservation] = []
    for t in load_test_results(session, athlete_id):
        obs.append(
            CPObservation(
                when=t.date,
                cp=t.cp,
                w_prime=t.w_prime if t.w_prime is not None else _WPRIME_NOMINAL,
                sd_cp=t.sd_cp,
                sd_wp=t.sd_wp if t.sd_wp is not None else _SD_WP_IGNORE,
            )
        )
    return obs



def _mmp_items(session: Session, athlete_id: int) -> list:
    """(fecha, activity_id, mmp_limpia) para el filtro, desde la MMP persistida.

    El filtro trabaja sobre la señal LIMPIA (por eso `mmp_clean`). Si falta
    alguna —instalación nueva, o versión del algoritmo cambiada— se calcula al
    vuelo y queda guardada: la primera vez cuesta lo de antes, las siguientes no.
    """
    from cycling_coach.twin.mmp_service import MMP_VERSION, backfill_mmp

    backfill_mmp(session, athlete_id)
    return [
        (when, aid, clean)
        for when, aid, _raw, clean in load_power_mmp(session, athlete_id, MMP_VERSION)
    ]


def estimate_cp(
    session: Session, athlete_id: int, config: CPFilterConfig | None = None
) -> CPEstimationResult | None:
    """Estima CP/W'/FTP actuales. Devuelve None si no hay observaciones."""
    mmp = _mmp_items(session, athlete_id)
    activity_obs = build_cp_observations_from_mmp(mmp)

    # Anclas: tests manuales (add-test) + actividades marcadas maximales (mark-test).
    test_obs = _test_observations(session, athlete_id)
    for when, _aid, watts in load_marked_test_activities(session, athlete_id):
        marked = observation_from_activity(when, watts)
        if marked is not None:
            test_obs.append(marked)

    all_obs = sorted(activity_obs + test_obs, key=lambda o: o.when)
    if not all_obs:
        return None

    cfg = resolve_config(session, athlete_id, config)
    trajectory = run_cp_filter(all_obs, config=cfg)
    current = trajectory[-1]

    # Incertidumbre HONESTA del CP actual: la CI del estado latente es demasiado
    # estrecha (el harness lo demostró). Usamos el error de predicción REAL del
    # backtest (RMSE) como suelo → nunca afirmamos más precisión de la validada.
    bt_obs = build_cp_observations_from_mmp(mmp, window_days=42, stride_days=42)
    bt = backtest_one_step(bt_obs, cfg)
    predictive_sd = max(current.cp.sd, bt.rmse) if bt is not None else current.cp.sd

    # La recomendación de test usa la incertidumbre honesta, no la del estado.
    rec = assess_test_need(
        current, all_obs[-1].when, current.as_of, sd_cp=predictive_sd
    )

    return CPEstimationResult(
        state=current,
        recommendation=rec,
        n_activity_obs=len(activity_obs),
        n_test_obs=len(test_obs),
        predictive_sd_cp=predictive_sd,
    )


def backtest(
    session: Session,
    athlete_id: int,
    config: CPFilterConfig | None = None,
    window_days: int = 42,
) -> BacktestResult | None:
    """Backtest one-step-ahead sobre observaciones NO solapadas (stride=ventana,
    para no filtrar información entre observaciones adyacentes)."""
    obs = build_cp_observations_from_mmp(
        _mmp_items(session, athlete_id), window_days=window_days, stride_days=window_days
    )
    return backtest_one_step(obs, config=resolve_config(session, athlete_id, config))


def tune(
    session: Session, athlete_id: int, window_days: int = 42, save: bool = True
) -> tuple[CPFilterConfig, BacktestResult, BacktestResult] | None:
    """Aprende los hiperparámetros (máx. verosimilitud predictiva) y los persiste.
    Devuelve (config_aprendida, backtest_antes, backtest_después)."""
    obs = build_cp_observations_from_mmp(
        _mmp_items(session, athlete_id), window_days=window_days, stride_days=window_days
    )
    result = learn_hyperparameters(obs)
    if result is None:
        return None
    learned, before, after = result
    if save:
        save_model_config(session, athlete_id, asdict(learned))
    return learned, before, after
