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


def test_метки_выровнены_по_строкам():
    """flags идёт в порядке строк, а не в порядке p-value.

    Иначе отчёт помечает «значимо» не те строки: в таблице refresh строки
    отсортированы по доходности, а гипотезы отбрасываются по возрастанию
    p-value — это разные порядки. Строка, не попавшая в счёт, обязана
    получить None, а не False: «не проверялась» и «проверена и не значима» —
    разные утверждения.
    """
    rows = [dict(kind="short", tf="5m", n=3, exp_net=2.0, sd=1.0),
            dict(kind="weak", tf="5m", n=200, exp_net=0.07, sd=1.0),
            dict(kind="strong", tf="5m", n=500, exp_net=0.30, sd=1.0),
            dict(kind="nosd", tf="5m", n=400, exp_net=0.50, sd=0.0)]
    v = judge(rows, alpha=0.05, min_n=30)
    assert v.flags == [None, False, True, None], v.flags
    assert len(v.flags) == len(rows)
    assert sum(1 for f in v.flags if f) == v.n_significant


def test_метки_не_зависят_от_порядка_строк():
    rows = [dict(kind="strong", tf="5m", n=500, exp_net=0.30, sd=1.0),
            dict(kind="weak", tf="5m", n=200, exp_net=0.07, sd=1.0)]
    a = judge(rows, alpha=0.05, min_n=30)
    b = judge(list(reversed(rows)), alpha=0.05, min_n=30)
    assert a.n_significant == b.n_significant
    assert a.flags == list(reversed(b.flags))


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


# --- поправка на перекрытие сделок (docs/research/19) ----------------------

def test_кластеры_склеивают_пересекающиеся_и_касающиеся_сделки():
    spans = [(0, 10), (5, 20), (20, 30), (31, 40), (100, 110)]
    c = significance.overlap_clusters(spans)
    # 0-10 и 5-20 пересекаются, 20-30 касается 5-20 (выход и вход на одной
    # свече), 31-40 отделена паузой, 100-110 — отдельное событие
    assert c[0] == c[1] == c[2]
    assert len({c[2], c[3], c[4]}) == 3


def test_кластеры_выровнены_по_входу_а_не_по_времени():
    spans = [(100, 110), (0, 10), (5, 20)]
    c = significance.overlap_clusters(spans)
    assert c[1] == c[2] != c[0]


def test_пауза_склейки_объединяет_близкие_события():
    spans = [(0, 10), (15, 20)]
    assert len(set(significance.overlap_clusters(spans))) == 2
    assert len(set(significance.overlap_clusters(spans, gap_ms=5))) == 1


def test_без_перекрытий_ошибка_совпадает_с_обычной():
    """Каждая сделка — свой кластер: поправка обязана ничего не менять."""
    import random
    from statistics import stdev

    rng = random.Random(3)
    xs = [rng.gauss(0.1, 1.0) for _ in range(80)]
    se = significance.cluster_se(xs, list(range(80)))
    assert math.isclose(se, stdev(xs) / math.sqrt(80), rel_tol=1e-12)
    row = dict(exp_net=sum(xs) / 80, sd=stdev(xs), n=80, se=se, n_eff=80)
    assert math.isclose(significance.row_p(row),
                        p_value(row["exp_net"], row["sd"], 80), rel_tol=1e-9)


def test_копии_одного_события_не_добавляют_значимости():
    """Восемь монет на одном движении — восемь сделок, но одно событие.

    Старая формула видит в восьми копиях восемь наблюдений и делит ошибку на
    √8. Кластерная ошибка от копий не меняется вовсе.
    """
    import random
    from statistics import stdev

    rng = random.Random(7)
    events = [rng.gauss(0.25, 1.0) for _ in range(40)]
    one = significance.cluster_se(events, list(range(40)))
    xs = [x for x in events for _ in range(8)]
    cl = [i for i in range(40) for _ in range(8)]
    many = significance.cluster_se(xs, cl)
    assert math.isclose(one, many, rel_tol=1e-12)
    naive = stdev(xs) / math.sqrt(len(xs))
    assert naive < many / 2.5, "старая формула занижает ошибку почти в √8 раз"


def test_на_нулевом_среднем_с_перекрытием_ложных_находок_около_альфы():
    """Мир без преимущества, сделки пачками по общему рыночному движению.

    Доля «значимых» по старой формуле в разы выше 5 %, по кластерной —
    около 5 %. Это и есть та ложная значимость, о которой docs/research/19.
    """
    import random
    from statistics import stdev

    rng = random.Random(42)
    naive_hits = cl_hits = 0
    runs = 400
    for _ in range(runs):
        xs, spans = [], []
        t = 0
        for _ in range(30):                    # 30 событий
            shock = rng.gauss(0.0, 1.0)        # общее движение рынка
            k = rng.randint(1, 8)              # сколько монет его поймали
            for _ in range(k):
                xs.append(shock + rng.gauss(0.0, 0.3))
                spans.append((t + rng.randint(0, 3), t + 10))
            t += 20
        n = len(xs)
        m = sum(xs) / n
        if p_value(m, stdev(xs), n) < 0.05:
            naive_hits += 1
        cl = significance.overlap_clusters(spans)
        p = significance.row_p(dict(exp_net=m, sd=stdev(xs), n=n,
                                    se=significance.cluster_se(xs, cl),
                                    n_eff=len(set(cl))))
        if p < 0.05:
            cl_hits += 1
    assert naive_hits / runs > 0.3
    assert cl_hits / runs < 0.09


def test_бутстрэп_сходится_с_кластерной_оценкой():
    import random

    rng = random.Random(9)
    xs, cl = [], []
    for c in range(60):
        shock = rng.gauss(0.35, 1.0)
        for _ in range(rng.randint(1, 5)):
            xs.append(shock + rng.gauss(0.0, 0.3))
            cl.append(c)
    se = significance.cluster_se(xs, cl)
    m = sum(xs) / len(xs)
    p_an = two_sided_p(m / se, 59)
    p_bs = significance.cluster_bootstrap_p(xs, cl, n_boot=4000, seed=1)
    assert p_bs is not None
    # обе оценки по одну сторону от 0.05 и одного порядка
    assert (p_an < 0.05) == (p_bs < 0.05)
    assert 0.2 < p_bs / p_an < 5


def test_бутстрэп_не_видит_сдвига_там_где_его_нет():
    import random

    rng = random.Random(4)
    xs = [rng.gauss(0.0, 1.0) for _ in range(200)]
    p = significance.cluster_bootstrap_p(xs, [i // 4 for i in range(200)])
    assert p > 0.05
    assert significance.cluster_bootstrap_p(xs, [0] * 200) is None


def test_вердикт_берёт_n_eff_и_считает_строки_без_него():
    """Строка с n = 400, но 12 независимыми событиями — не находка, а строка
    из старой базы без n_eff считается по старой формуле и попадает в
    n_naive, чтобы отчёт мог предупредить."""
    rows = [dict(kind="clustered", tf="1h", n=400, exp_net=0.30, sd=1.0,
                 se=0.30, n_eff=12),
            dict(kind="old", tf="1h", n=400, exp_net=0.30, sd=1.0)]
    v = judge(rows, alpha=0.05, min_n=30)
    assert v.flags == [False, True]
    assert v.n_naive == 1


def test_одно_событие_на_всю_строку_не_проверяется():
    rows = [dict(kind="one", tf="1h", n=50, exp_net=0.5, sd=1.0,
                 se=0.2, n_eff=1)]
    v = judge(rows, alpha=0.05, min_n=30)
    assert v.flags == [None]


def test_измерение_считает_n_eff_по_времени_сделок():
    """_build склеивает пересекающиеся сделки строки в кластеры."""
    from types import SimpleNamespace

    from src.backtest.walk import Trade, _build

    def tr(r, a, b):
        f = SimpleNamespace(kind="bounce", tf="1h", direction="long", ts=a)
        return Trade(f, "target" if r > 0 else "stop", r, 1,
                     entry_ms=a, exit_ms=b, r_net=r)

    ts = [tr(1.0, 0, 10), tr(1.2, 5, 15), tr(-1.0, 100, 110),
          tr(0.5, 200, 210), tr(-0.4, 300, 310)]
    s = _build({("bounce", "1h"): ts})[0]
    assert s.n == 5 and s.n_eff == 4
    assert s.se > 0 and s.p_boot is not None


def test_схема_дописывает_поля_перекрытия_и_хранит_их():
    conn = db.connect(":memory:")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(formation_stats)")}
    assert {"se", "n_eff", "p_boot"} <= cols
    db.upsert_formation_stats(conn, [dict(
        kind="bounce", tf="1h", measured_on="2026-10-04", symbol_scope="BTCUSDT",
        n=40, win_rate=50.0, exp_gross=0.2, exp_net=0.1, cost=0.1, sd=1.0,
        se=0.25, n_eff=17, p_boot=0.7)])
    got = db.load_formation_stats(conn)[("bounce", "1h")]
    assert got["n_eff"] == 17 and got["se"] == 0.25 and got["p_boot"] == 0.7
