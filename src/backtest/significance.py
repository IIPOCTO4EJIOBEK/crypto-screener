"""Значимость измерения: что из «плюсов» переживает поправку на перебор.

Зачем это нужно. Измерение проверяет не одну гипотезу, а десятки: 24 сочетания
«формация × таймфрейм», и каждая считается отдельно. При таком переборе часть
положительных средних появляется просто от разброса — при двадцати с лишним
сравнениях «полторы хорошие из многих» ожидаемы и без всякого преимущества.
Число без этой поправки читается как находка, хотя находкой не является.

Что считается:

  t-статистика среднего R — среднее, делённое на свою стандартную ошибку.
    p-value берётся по распределению Стьюдента с n−1 степенями свободы, а не
    по нормальному: при n около 30 разница в хвостах заметна (критическое
    значение 2.04 против 1.96), а именно на таких n у нас и стоят плюсовые
    строки.

  Поправка на перекрытие сделок. Сделки одной строки не независимы: на
    восьми монетах сразу и при удержании дольше шага среза они идут внахлёст,
    и одно рыночное движение даёт пачку сделок с одинаковым исходом. Обычная
    t-статистика считает такую пачку n независимыми наблюдениями и занижает
    p на порядки (docs/research/19). Поэтому сделки склеиваются в кластеры —
    связные группы пересекающихся по времени интервалов [вход, выход], по
    всем монетам строки, — и стандартная ошибка среднего считается
    кластерно-устойчивой (HAC-оценка в событийном времени: та же идея, что у
    Newey-West, только корреляция допускается не на фиксированное число лагов,
    а ровно между пересекающимися сделками). Степеней свободы — число
    кластеров минус один, а не n − 1. Для сверки рядом считается блочный
    бутстрэп с теми же кластерами в роли блоков. Число кластеров — это и есть
    эффективный размер выборки n_eff.

  Поправка Бенджамини-Хохберга (FDR) — по всем p сразу. Контролирует долю
    ложных находок среди отобранных, а не вероятность хотя бы одной ошибки,
    как Бонферрони; при десятках сравнений Бонферрони слишком груба.

  Deflated Sharpe Ratio (Bailey, López de Prado) — вероятность, что лучший
    результат перебора объясняется самим перебором. Учитывает число испытаний,
    разброс Sharpe между ними, скошенность и «тяжесть хвостов» распределения
    доходностей, а также длину выборки.

Распределение Стьюдента и обратная функция нормального распределения
посчитаны здесь сами: scipy в зависимостях проекта нет, а тащить его ради
двух функций незачем.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# Константа Эйлера-Маскерони — входит в оценку ожидаемого максимума Sharpe.
EULER = 0.5772156649015329


def norm_cdf(x: float) -> float:
    """Функция распределения нормального закона."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Обратная функция нормального распределения (приближение Acklam).

    Точность около 1.15e-9 по всей области — для наших задач с запасом.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"вероятность вне (0, 1): {p}")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _betacf(a: float, b: float, x: float) -> float:
    """Непрерывная дробь для неполной бета-функции (Numerical Recipes)."""
    maxit, eps, fpmin = 200, 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Регуляризованная неполная бета-функция I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def two_sided_p(t: float, df: int) -> float:
    """Двустороннее p-value распределения Стьюдента с df степенями свободы."""
    if df <= 0:
        return 1.0
    if t == 0.0:
        return 1.0
    return min(1.0, betainc(df / 2.0, 0.5, df / (df + t * t)))


def t_stat(mean: float, sd: float, n: int) -> float | None:
    """t-статистика среднего. None, если разброса нет или выборка слишком мала."""
    if n < 2 or sd <= 0.0:
        return None
    return mean / (sd / math.sqrt(n))


def p_value(mean: float, sd: float, n: int) -> float | None:
    """Двустороннее p-value для среднего; None, если его не из чего считать."""
    t = t_stat(mean, sd, n)
    return None if t is None else two_sided_p(t, n - 1)


def overlap_clusters(spans: list[tuple[int, int]], gap_ms: int = 0) -> list[int]:
    """Номера кластеров сделок: связные группы пересекающихся интервалов.

    spans — (вход, выход) каждой сделки в миллисекундах, в любом порядке.
    Две сделки в одном кластере, если их интервалы пересекаются или касаются
    (выход одной на той же свече, что вход другой), — напрямую или через
    цепочку других сделок. gap_ms расширяет склейку: сделки, разделённые
    паузой не длиннее gap_ms, тоже считаются одним событием.

    Возвращает список той же длины и в том же порядке, что и вход.
    """
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    out = [0] * len(spans)
    cid, edge = -1, None
    for i in order:
        start, end = spans[i]
        if edge is None or start > edge + gap_ms:
            cid += 1
            edge = end
        else:
            edge = max(edge, end)
        out[i] = cid
    return out


def _cluster_sums(values: list[float], clusters: list[int]
                  ) -> tuple[list[float], list[int]]:
    sums: dict[int, float] = {}
    sizes: dict[int, int] = {}
    for v, c in zip(values, clusters):
        sums[c] = sums.get(c, 0.0) + v
        sizes[c] = sizes.get(c, 0) + 1
    keys = sorted(sums)
    return [sums[k] for k in keys], [sizes[k] for k in keys]


def cluster_se(values: list[float], clusters: list[int]) -> float | None:
    """Кластерно-устойчивая стандартная ошибка среднего.

    Var(среднего) = G/(G−1) · Σ_c (Σ_{i∈c} (x_i − x̄))² / n², где G — число
    кластеров. Множитель G/(G−1) — обычная поправка на малое число
    кластеров. Если каждая сделка — свой кластер (перекрытий нет), оценка
    в точности равна обычной sd/√n: G = n, и G/(G−1)·Σ(x−x̄)²/n² = sd²/n.

    None, если кластеров меньше двух: дисперсию по одному событию не оценить.
    """
    n = len(values)
    if n < 2 or len(clusters) != n:
        return None
    mean = sum(values) / n
    sums, sizes = _cluster_sums(values, clusters)
    g = len(sums)
    if g < 2:
        return None
    var = sum((s - k * mean) ** 2 for s, k in zip(sums, sizes)) / (n * n)
    var *= g / (g - 1)
    return math.sqrt(var) if var > 0 else None


def cluster_bootstrap_p(values: list[float], clusters: list[int],
                        n_boot: int = 2000, seed: int = 0) -> float | None:
    """Двустороннее p-value среднего по блочному бутстрэпу.

    Блоки — кластеры пересекающихся сделок: они берутся с возвращением
    целиком, поэтому зависимость внутри события сохраняется. Ряд сначала
    сдвигается к нулевому среднему (так выглядит мир, где преимущества нет),
    и p — доля выборок, где |среднее| не меньше наблюдённого. Разрешение
    p — 1/(n_boot+1): меньше этого бутстрэп сказать не может.

    seed фиксирован, чтобы одно измерение давало одно число при повторе.
    """
    n = len(values)
    if n < 2 or len(clusters) != n:
        return None
    mean = sum(values) / n
    sums, sizes = _cluster_sums(values, clusters)
    g = len(sums)
    if g < 2:
        return None
    resid = [s - k * mean for s, k in zip(sums, sizes)]
    if all(abs(r) < 1e-15 for r in resid):
        return None
    rng = random.Random(seed)
    idx = range(g)
    hits = 0
    for _ in range(n_boot):
        pick = rng.choices(idx, k=g)
        tot = sum(resid[j] for j in pick)
        cnt = sum(sizes[j] for j in pick)
        if abs(tot / cnt) >= abs(mean) - 1e-15:
            hits += 1
    return (hits + 1) / (n_boot + 1)


def row_p(row: dict) -> float | None:
    """p-value строки измерения с учётом перекрытия, если оно измерено.

    Строка с полями se и n_eff (их пишет измерение с этой правкой) считается
    по кластерной ошибке и n_eff − 1 степеням свободы. Строка без них — из
    базы, созданной раньше, — по старой формуле sd/√n; такая p занижена, и
    judge считает такие строки отдельно (Verdict.n_naive).
    """
    se, g = row.get("se"), row.get("n_eff")
    if se and g:
        if g < 2 or se <= 0:
            return None
        t = row["exp_net"] / se
        return two_sided_p(t, int(g) - 1)
    return p_value(row["exp_net"], row.get("sd") or 0.0, row["n"])


def _naive(row: dict) -> bool:
    return not (row.get("se") and row.get("n_eff"))


@dataclass(frozen=True)
class Verdict:
    """Итог поправки на перебор по всей таблице измерения.

    `flags` выровнен по входным строкам: True — строка значима после поправки,
    False — проверялась и не значима, None — в счёт не вошла (мало сделок или
    нет разброса). Печатать метку значимости из flags, а не своим прогоном
    Бенджамини-Хохберга: иначе метки строк и итог считаются по разным наборам
    и противоречат друг другу.
    """

    n_tested: int          # сколько сравнений проверялось
    n_positive: int        # сколько из них с положительным средним
    n_significant: int     # сколько значимо после поправки (FDR)
    alpha: float
    dsr: float | None      # Deflated Sharpe лучшей строки, 0..1
    flags: list[bool | None] = field(default_factory=list)
    # сколько проверенных строк посчитано без поправки на перекрытие сделок —
    # у них нет se и n_eff (база измерена до правки); их p занижены
    n_naive: int = 0


def _sharpe(mean: float, sd: float) -> float | None:
    """Sharpe в единицах на сделку: среднее R, делённое на разброс R."""
    if sd is None or sd <= 0.0:
        return None
    return mean / sd


def deflated_sharpe(sr: float, n_obs: int, sd_sr: float, n_trials: int,
                    skew: float = 0.0, kurt: float = 3.0) -> float | None:
    """Deflated Sharpe Ratio: вероятность, что Sharpe не плод перебора.

    sr — наблюдаемый Sharpe на сделку, n_obs — число сделок, sd_sr — разброс
    Sharpe по испытанным конфигурациям, n_trials — их число. skew и kurt —
    скошенность и эксцесс распределения доходностей (kurt=3 для нормального).

    Возвращает 0..1. Малое значение означает, что такой Sharpe легко получить
    перебором; большое — что перебором его не объяснить.
    """
    if n_obs < 2 or n_trials < 2 or sd_sr <= 0.0:
        return None
    # ожидаемый максимум Sharpe среди n_trials независимых испытаний
    e_max = sd_sr * ((1.0 - EULER) * norm_ppf(1.0 - 1.0 / n_trials)
                     + EULER * norm_ppf(1.0 - 1.0 / (n_trials * math.e)))
    denom_sq = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom_sq <= 0.0:
        return None
    z = (sr - e_max) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return norm_cdf(z)


def sharpe_diff(r1: list[float], r2: list[float],
                per_year: float) -> tuple[float, float] | None:
    """Различие двух Sharpe на одних и тех же данных: (z, p).

    Два Sharpe, посчитанных на одном ряде доходностей, нельзя сравнивать как
    независимые: они коррелированы, и обычная разность стандартных ошибок
    завышает значимость. Берётся тест Джобсона-Корки в форме Меммеля:

        z = (SR₁ − SR₂) / √θ,
        θ = (1/n)·[2(1−ρ) + ½(SR₁² + SR₂² − 2·SR₁·SR₂·ρ²)]

    где ρ — корреляция доходностей двух стратегий. per_year — множитель
    перевода Sharpe в годовой (число наблюдений в году).
    """
    n = min(len(r1), len(r2))
    if n < 3:
        return None
    a, b = r1[-n:], r2[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a) / (n - 1)
    vb = sum((x - mb) ** 2 for x in b) / (n - 1)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)
    rho = cov / math.sqrt(va * vb)
    sr1 = ma / math.sqrt(va) * math.sqrt(per_year)
    sr2 = mb / math.sqrt(vb) * math.sqrt(per_year)
    theta = (2.0 * (1.0 - rho)
             + 0.5 * (sr1 * sr1 + sr2 * sr2 - 2.0 * sr1 * sr2 * rho * rho)) / n
    if theta <= 0:
        return None
    z = (sr1 - sr2) / math.sqrt(theta)
    return z, two_sided_p(z, n - 1)


def benjamini_hochberg(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    """Поправка Бенджамини-Хохберга: какие гипотезы отвергаются.

    Возвращает список той же длины и в том же порядке, что и вход: True там,
    где гипотеза «среднее не отличается от нуля» отвергается. Порог не зависит
    от порядка строк в таблице — это важно, иначе результат зависел бы от
    сортировки отчёта.
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    accepted = [False] * m
    kmax = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= alpha * rank / m:
            kmax = rank
    if kmax > 0:
        for idx in order[:kmax]:
            accepted[idx] = True
    return accepted


def judge(rows: list[dict], alpha: float = 0.05,
          min_n: int = 2) -> Verdict:
    """Свести таблицу измерения к одному вердикту с поправкой на перебор.

    Строки — словари с полями exp_net, n и sd (разброс R по сделкам), а если
    измерение их дало — se и n_eff (ошибка и эффективный размер с учётом
    перекрытия сделок, см. row_p). В счёт идут только строки не короче min_n
    и с разбросом: p-value по одной сделке не определён, а по трём
    определяется так, что случайный плюс выглядит находкой. Порог задаёт
    вызывающий — у скринера он свой (MIN_TRADES).
    """
    tested, pvals, idx = [], [], []
    for i, r in enumerate(rows):
        sd = r.get("sd")
        if not sd or r["n"] < min_n:
            continue
        p = row_p(r)
        if p is None:
            continue
        tested.append(r)
        pvals.append(p)
        idx.append(i)
    n_positive = sum(1 for r in tested if r["exp_net"] > 0)
    flags = benjamini_hochberg(pvals, alpha)
    marks: list[bool | None] = [None] * len(rows)
    for i, ok in zip(idx, flags):
        marks[i] = ok
    dsr = None
    if tested:
        best = max(tested, key=lambda r: r["exp_net"])
        sharpes = [s for s in (_sharpe(r["exp_net"], r.get("sd")) for r in tested)
                   if s is not None]
        if sharpes and best.get("sd"):
            mean_sharpe = sum(sharpes) / len(sharpes)
            var = sum((s - mean_sharpe) ** 2 for s in sharpes) / len(sharpes)
            # длина выборки для DSR — независимые события, а не сделки
            n_obs = int(best.get("n_eff") or best["n"])
            dsr = deflated_sharpe(_sharpe(best["exp_net"], best["sd"]),
                                  n_obs, math.sqrt(var), len(tested))
    return Verdict(len(tested), n_positive, sum(flags), alpha, dsr, marks,
                   sum(1 for r in tested if _naive(r)))
