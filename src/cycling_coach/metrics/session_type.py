"""Qué fue REALMENTE la sesión: zonas de potencia e intervalos detectados.

Clasificar por el IF medio de toda la actividad es engañoso en sesiones de
intervalos: el calentamiento, las recuperaciones y la vuelta a la calma diluyen
la media y una sesión de VO2máx acaba pareciendo "sweet spot". Aquí miramos la
DISTRIBUCIÓN de la potencia (tiempo en zona) y los tramos de trabajo reales, que
es lo que determina el estímulo fisiológico.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Zonas clásicas por %FTP (Coggan). El corte superior de cada zona.
ZONES: list[tuple[str, float, float]] = [
    ("Z1 recuperación", 0.0, 0.55),
    ("Z2 resistencia", 0.55, 0.75),
    ("Z3 tempo", 0.75, 0.90),
    ("Z4 umbral", 0.90, 1.05),
    ("Z5 VO2máx", 1.05, 1.20),
    ("Z6 anaeróbico", 1.20, 99.0),
]

# Mínimos de tiempo EN TRABAJO para considerar que la sesión "fue de" esa zona.
# Por debajo, unos segundos sueltos (un repecho, un semáforo) no definen la
# sesión. Son minutos acumulados en la zona o por encima.
_MIN_MINUTES = {
    "vo2max": 5.0,        # 5 min por encima de 105% FTP ya es un bloque de VO2
    "threshold": 10.0,
    "sweet_spot": 15.0,
    "tempo": 20.0,
}
# Un intervalo de trabajo: tramo continuo por encima de este %FTP…
_WORK_FLOOR = 0.88
# …que dure al menos esto (segundos). Filtra picos y repechos sueltos.
_MIN_INTERVAL_S = 60
# Huecos cortos por debajo del suelo no parten el intervalo (bajones de señal,
# una curva, levantarse del sillín).
_BRIDGE_S = 15


@dataclass
class Interval:
    start_s: int
    seconds: int
    avg_w: float
    pct_ftp: float


@dataclass
class SessionProfile:
    """Retrato de lo que fue la sesión, medido sobre la serie de potencia."""
    zone_seconds: dict[str, int] = field(default_factory=dict)
    intervals: list[Interval] = field(default_factory=list)
    kind: str = "endurance"        # etiqueta fisiológica de la sesión
    label: str = "Resistencia"     # nombre legible
    detected: str | None = None    # "5×5' a 112% FTP" si hay estructura clara

    @property
    def work_minutes(self) -> float:
        return sum(i.seconds for i in self.intervals) / 60.0


def zone_seconds(watts: list, ftp: float) -> dict[str, int]:
    """Segundos en cada zona (la serie es 1 Hz → 1 muestra = 1 s)."""
    out = {name: 0 for name, _, _ in ZONES}
    for w in watts:
        if w is None:
            continue
        pct = float(w) / ftp
        for name, lo, hi in ZONES:
            if lo <= pct < hi:
                out[name] += 1
                break
    return out


def find_intervals(watts: list, ftp: float) -> list[Interval]:
    """Tramos continuos de trabajo por encima de `_WORK_FLOOR` × FTP.

    Une huecos cortos (`_BRIDGE_S`) para no partir una serie por un bache de
    señal, y descarta lo que dure menos de `_MIN_INTERVAL_S`."""
    floor = _WORK_FLOOR * ftp
    out: list[Interval] = []
    start: int | None = None
    gap = 0
    acc: list[float] = []
    for i, w in enumerate(watts):
        above = w is not None and float(w) >= floor
        if above:
            if start is None:
                start, acc, gap = i, [], 0
            acc.append(float(w))
            gap = 0
        elif start is not None:
            gap += 1
            if gap > _BRIDGE_S:
                end = i - gap
                secs = end - start
                if secs >= _MIN_INTERVAL_S and acc:
                    avg = sum(acc) / len(acc)
                    out.append(Interval(start, secs, avg, avg / ftp))
                start, acc, gap = None, [], 0
    if start is not None and acc:
        secs = len(watts) - start
        if secs >= _MIN_INTERVAL_S:
            avg = sum(acc) / len(acc)
            out.append(Interval(start, secs, avg, avg / ftp))
    return out


def _describe(intervals: list[Interval]) -> str | None:
    """Estructura legible: "6×2' a 125% FTP (+1 más)".

    Busca el GRUPO DOMINANTE de series de duración parecida en vez de exigir que
    todas lo sean: una serie de 6×2' con un sprint final al acabar se sigue
    leyendo como 6×2', que es lo que el ciclista hizo."""
    if not intervals:
        return None
    if len(intervals) == 1:
        i = intervals[0]
        return f"1 bloque de {i.seconds / 60:.0f}' a {i.pct_ftp * 100:.0f}% FTP"

    # Grupo más numeroso de series con duración dentro del ±25% de una de ellas.
    best: list[Interval] = []
    for ref in intervals:
        group = [
            i for i in intervals
            if abs(i.seconds - ref.seconds) <= 0.25 * ref.seconds
        ]
        if len(group) > len(best):
            best = group

    if len(best) >= 2:
        avg_min = sum(i.seconds for i in best) / len(best) / 60
        avg_pct = sum(i.pct_ftp for i in best) / len(best) * 100
        txt = f"{len(best)}×{avg_min:.0f}' a {avg_pct:.0f}% FTP"
        rest = len(intervals) - len(best)
        return f"{txt} (+{rest} bloque{'s' if rest > 1 else ''} más)" if rest else txt

    total = sum(i.seconds for i in intervals) / 60
    avg_pct = sum(i.pct_ftp for i in intervals) / len(intervals) * 100
    return f"{len(intervals)} tramos, {total:.0f}' a {avg_pct:.0f}% FTP"


def classify(watts: list, ftp: float) -> SessionProfile:
    """Clasifica la sesión por su DISTRIBUCIÓN de potencia, no por la media.

    Mira los minutos acumulados en cada zona (y por encima): así una sesión de
    5×5' a VO2máx se reconoce como VO2máx aunque la media de la salida sea baja
    por el calentamiento y las recuperaciones."""
    if not watts or not ftp:
        return SessionProfile()
    zs = zone_seconds(watts, ftp)
    intervals = find_intervals(watts, ftp)

    z5 = (zs["Z5 VO2máx"] + zs["Z6 anaeróbico"]) / 60.0
    z4 = zs["Z4 umbral"] / 60.0
    z3 = zs["Z3 tempo"] / 60.0
    # Minutos de trabajo intenso dentro de los intervalos detectados (más fiable
    # que el tiempo en zona suelto: exige que fuera un esfuerzo sostenido).
    vo2_work = sum(i.seconds for i in intervals if i.pct_ftp >= 1.05) / 60.0
    thr_work = sum(i.seconds for i in intervals if 0.95 <= i.pct_ftp < 1.05) / 60.0
    ss_work = sum(i.seconds for i in intervals if 0.88 <= i.pct_ftp < 0.95) / 60.0

    if max(z5, vo2_work) >= _MIN_MINUTES["vo2max"]:
        kind, label = "vo2max", "VO2máx"
    elif max(z4, thr_work) >= _MIN_MINUTES["threshold"]:
        kind, label = "threshold", "Umbral"
    elif ss_work >= _MIN_MINUTES["sweet_spot"]:
        # Sweet spot exige BLOQUES sostenidos a 88–95%, no tiempo suelto: en una
        # ruta larga se acumulan minutos en tempo sin que sea una sesión de SS.
        kind, label = "sweet_spot", "Sweet Spot"
    elif z3 >= _MIN_MINUTES["tempo"]:
        kind, label = "tempo", "Tempo"
    elif zs["Z2 resistencia"] / 60.0 >= 20:
        kind, label = "endurance", "Resistencia (Z2)"
    else:
        kind, label = "recovery", "Recuperación"

    return SessionProfile(
        zone_seconds=zs, intervals=intervals, kind=kind, label=label,
        detected=_describe(intervals),
    )
