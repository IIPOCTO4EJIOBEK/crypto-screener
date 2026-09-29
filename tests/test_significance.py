"""Тесты значимости: распределения, поправка на перебор и вердикт.

Считается всё вручную, без scipy, поэтому проверяется не только логика, но и
сами приближения — их ошибка не даёт исключения, а тихо сдвигает p-value, и
вывод «значимо / не значимо» переворачивается молча.
"""

from __future__ import annotations

import math

from src.backtest import significance
from src.backtest.significance import (benjamini_hochberg, deflated_sharpe,
                                       judge, norm_cdf, norm_ppf, p_value,
                                       t_stat, two_sided_p)
from src.storage import db


def test_нормальное_распределение_сходится_с_табличным():
    assert abs(norm_cdf(0.0) - 0.5) < 1e-12
    assert abs(norm_cdf(1.959964) - 0.975) < 1e-5
    assert abs(norm_ppf(0.975) - 1.959964) < 1e-5
    assert abs(norm_ppf(0.5)) < 1e-12


def test_обратная_функция_и_прямая_согласованы():
    for p in (0.01, 0.2, 0.5, 0.8, 0.99):
        assert abs(norm_cdf(norm_ppf(p)) - p) < 1e-9


def test_стьюдент_на_малой_выборке_отличается_от_нормального():
    """При n около 30 нормальное приближение занижает p — его и не берём.

    Критическое значение для df=29 — 2.045, и p при нём обязано быть 0.05, а
    не 0.041, как дало бы нормальное распределение.
    """
    assert abs(two_sided_p(2.045, 29) - 0.05) < 5e-4
    assert two_sided_p(2.045, 29) > two_sided_p(2.045, 1000)


def test_p_value_и_t_статистика():
    # среднее 0.5 R при разбросе 1 R на 100 сделках: t = 5
    assert abs(t_stat(0.5, 1.0, 100) - 5.0) < 1e-12
    assert p_value(0.5, 1.0, 100) < 1e-5
    # тот же средний результат на четырёх сделках — уже не находка
    assert p_value(0.5, 1.0, 4) > 0.3
    assert p_value(0.0, 1.0, 100) == 1.0


def test_без_разброса_или_на_одной_сделке_p_не_определён():
    assert t_stat(0.5, 0.0, 100) is None
    assert p_value(0.5, 1.0, 1) is None


def test_бенджамини_хохберг_на_примере_из_статьи():
    """Пятнадцать p-value из статьи 1995 года, α = 0.05.

    Отвергается только первая гипотеза: порог для второй — 2·0.05/15 = 0.0067,
    а 0.008 его уже превышает. При α = 0.1 порог вдвое выше, и проходит вторая.
    """
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212,
          0.216, 0.222, 0.251, 0.269, 0.275, 0.34]
    assert benjamini_hochberg(ps, 0.05) == [True] + [False] * 14
    assert sum(benjamini_hochberg(ps, 0.1)) == 2


def test_поправка_не_зависит_от_порядка_строк():
    ps = [0.3, 0.001, 0.04]
    # порог для второго места — 2·0.05/3 = 0.033, и 0.04 его не проходит
    assert benjamini_hochberg(ps, 0.05) == [False, True, False]
    assert benjamini_hochberg([0.04, 0.3, 0.001], 0.05) == [False, False, True]


def test_deflated_sharpe_падает_с_числом_испытаний():
    """Чем больше перебрали, тем ниже шанс, что лучший результат не случаен."""
    few = deflated_sharpe(sr=0.3, n_obs=200, sd_sr=0.1, n_trials=2)
    many = deflated_sharpe(sr=0.3, n_obs=200, sd_sr=0.1, n_trials=500)
    assert few is not None and many is not None
    assert few > many
    assert 0.0 <= many <= 1.0


def test_вердикт_отбрасывает_плюс_на_малой_выборке():
    """Два разных случая сразу: короткая строка и слабый плюс.

    Строка на трёх сделках в счёт не идёт вовсе. Плюс 0.07 R на 200 сделках
    при разбросе 1 R даёт t ≈ 1.0 (p ≈ 0.32) — это шум. Для сравнения: плюс
    0.3 R на тех же 200 сделках дал бы t ≈ 4.2 и был бы значим.
    """
    rows = [dict(kind="a", tf="5m", n=3, exp_net=2.0, sd=1.0),
            dict(kind="b", tf="5m", n=200, exp_net=0.07, sd=1.0),
            dict(kind="c", tf="5m", n=150, exp_net=-0.05, sd=1.0)]
    v = judge(rows, alpha=0.05, min_n=30)
    assert v.n_tested == 2, "строка на трёх сделках не должна идти в счёт"
    assert v.n_positive == 1
    assert v.n_significant == 0


def test_вердикт_считает_значимым_и_минус():
    """Проверяется отличие от нуля, а не знак: устойчивый минус — тоже факт."""
    rows = [dict(kind="c", tf="5m", n=400, exp_net=-0.30, sd=1.0)]
    v = judge(rows, alpha=0.05, min_n=30)
    assert v.n_significant == 1


def test_вердикт_видит_настоящий_сдвиг():
    rows = [dict(kind="b", tf="5m", n=500, exp_net=0.30, sd=1.0)]
    v = judge(rows, alpha=0.05, min_n=30)
    assert v.n_significant == 1


def test_разница_sharpe_не_определена_на_совпадающих_рядах():
    """Один и тот же ряд: различия нет, и число не должно притворяться им."""
    r = [0.01, -0.005, 0.02, 0.004, -0.002]
    assert significance.sharpe_diff(r, r, 73) is None
    assert significance.sharpe_diff(r, [0.0, 0.0], 73) is None


def test_разница_sharpe_видит_устойчивое_преимущество():
    import random

    rng = random.Random(11)
    n = 600
    noise = [rng.gauss(0.0, 0.02) for _ in range(n)]
    better = [x + 0.0015 for x in noise]
    got = significance.sharpe_diff(better, noise, 73)
    assert got is not None
    z, p = got
    assert z > 0 and p < 1e-6


def test_разница_sharpe_меняет_знак_при_перестановке():
    import random

    rng = random.Random(5)
    a = [rng.gauss(0.001, 0.02) for _ in range(300)]
    b = [x + rng.gauss(0.0, 0.01) for x in a]
    z1, _ = significance.sharpe_diff(a, b, 73)
    z2, _ = significance.sharpe_diff(b, a, 73)
    assert abs(z1 + z2) < 1e-9


def test_схема_дописывает_разброс_в_старую_базу():
    """Колонка sd добавлена позже; база, созданная раньше, обязана её получить.

    Без миграции CREATE TABLE IF NOT EXISTS оставил бы старую таблицу как
    есть, и запись измерения упала бы на незнакомой колонке.
    """
    conn = db.connect(":memory:")
    conn.execute("DROP TABLE formation_stats")
    conn.execute("""CREATE TABLE formation_stats (
        kind TEXT NOT NULL, tf TEXT NOT NULL, measured_on TEXT NOT NULL,
        symbol_scope TEXT NOT NULL, n INTEGER NOT NULL, win_rate REAL NOT NULL,
        exp_gross REAL NOT NULL, exp_net REAL NOT NULL, cost REAL NOT NULL,
        PRIMARY KEY (kind, tf))""")
    conn.execute("INSERT INTO formation_stats VALUES "
                 "('bounce','5m','2026-09-29','BTCUSDT',40,50.0,0.1,-0.1,0.2)")
    conn.commit()

    db.init_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(formation_stats)")}
    assert "sd" in cols
    left = db.load_formation_stats(conn)
    assert left[("bounce", "5m")]["sd"] == 0.0, "старая строка должна уцелеть"
    assert left[("bounce", "5m")]["n"] == 40


def test_значимость_доходит_до_сделок_через_измерение():
    """sd считается по сделкам, а не берётся откуда-то ещё."""
    from src.backtest.walk import Stats
    s = Stats("bounce", "5m", 3, 2, 1, 0, 1.0, 0.6, 0.4, 0.5)
    assert math.isclose(s.sd, 0.5)
    assert significance.p_value(s.expectancy_net, s.sd, s.n) is not None
