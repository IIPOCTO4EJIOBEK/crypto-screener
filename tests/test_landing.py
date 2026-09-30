"""Тесты лендинга: числа страницы обязаны сходиться с источниками.

Лендинг — первая страница проекта, её читают до всякой методики. Поэтому
проверяется не разметка, а два свойства: (1) ни одно число не берётся с
потолка и не вписывается руками, (2) на странице нет утверждения, которое
источник не подтверждает. Второй класс ошибки тут уже случался — карточка
тренд-фильтра приписывала числа вселенной по дате проверке вне выбора
параметров, которая считалась на восьмёрке монет.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.live.landing import (DB_PATH, GITHUB_BLOB, LOG_DIR, PLACEHOLDER,
                                TREND_JSON, build, build_page, links, p_text)


def _stub(tmp_path: Path, body: str = PLACEHOLDER) -> Path:
    t = tmp_path / "t.html"
    t.write_text(body, encoding="utf-8")
    return t


@pytest.mark.parametrize("value,want", [
    (1.78e-06, "p = 1.8·10⁻⁶"),      # число лендинга
    (9.9e-12, "p = 9.9·10⁻¹²"),      # граница округления: log10 сдвигал порядок
    (0.0009999, "p = 1.0·10⁻³"),     # мантисса не печатается как 10.0
    (4.1e-04, "p = 4.1·10⁻⁴"),
    (0.032, "p = 0.032"),            # не мельче тысячных — обычной записью
    (None, "p не посчитан"),
])
def test_p_значение_печатается_по_самому_числу(value, want):
    assert p_text(value) == want


def test_шаблон_обязан_иметь_ровно_один_плейсхолдер(tmp_path):
    data = {"a": 1}
    assert '"a":1' in build_page(_stub(tmp_path), data)
    for bad in ("без плейсхолдера", PLACEHOLDER + PLACEHOLDER):
        with pytest.raises(SystemExit):
            build_page(_stub(tmp_path, bad), data)


def test_закрывающий_тег_не_рвёт_страницу(tmp_path):
    """JSON со строкой `</script>` внутри не должен закрыть тег данных."""
    html = build_page(_stub(tmp_path, "<script>" + PLACEHOLDER + "</script>"),
                      {"x": "a</script>b"})
    assert "a<\\/script>b" in html
    assert html.count("</script>") == 1  # только тот, что был в шаблоне


def test_адреса_документов_в_плоской_раскладке_ведут_в_github():
    flat = links("flat")
    assert all(v.startswith(GITHUB_BLOB) for v in flat["docs"].values())
    # имена документов кириллические — они обязаны быть в процентной записи
    assert all("%" in v for v in flat["docs"].values())
    repo = links("repo")
    assert not any(v.startswith("http") for v in repo["docs"].values())


def _built(mode: str = "repo") -> dict:
    if not DB_PATH.exists():
        pytest.skip("нет базы скринера")
    return build(db_path=DB_PATH, log_dir=LOG_DIR, mode=mode,
                 trend_paths=TREND_JSON)


def test_числа_таблицы_сходятся_между_собой():
    """R после издержек = R до издержек − издержки, строка в строку."""
    m = _built()["measurement"]
    for r in m["table"]:
        assert r["r_net"] == pytest.approx(r["r_gross"] - r["cost_r"], abs=2e-3)
        assert r["support"] == (r["n"] >= m["min_trades"])
        assert r["significant"] is None or r["support"], (
            f"{r['kind']} {r['tf']}: поправка смотрела на строку без опоры")
    assert m["rows_total"] == len(m["table"])
    assert m["rows_support"] == sum(1 for r in m["table"] if r["support"])
    # «в плюсе» считается среди строк-опор: у строки без опоры оценка
    # разброса не определена, и в счёт она не идёт
    assert m["rows_positive"] == sum(
        1 for r in m["table"] if r["support"] and r["r_net"] > 0)
    assert m["rows_significant"] == sum(
        1 for r in m["table"] if r["significant"] is True)
    assert m["rows_significant_positive"] == sum(
        1 for r in m["table"] if r["significant"] is True and r["r_net"] > 0)


def test_выжившая_строка_одна_и_совпадает_со_своей_строкой_таблицы():
    m = _built()["measurement"]
    win = [r for r in m["table"]
           if r["significant"] is True and r["r_net"] > 0]
    if not win:
        assert m["survivor"] is None
        return
    assert m["rows_significant_positive"] == 1, (
        "плюсовых значимых больше одной — блок «единственная выжившая» врёт")
    s = m["survivor"]
    assert (s["kind"], s["tf"]) == (win[0]["kind"], win[0]["tf"])
    assert s["r_net"] == win[0]["r_net"]


def test_страница_не_обещает_сигналов():
    data = _built()
    assert data["meta"]["no_signals"] is True
    text = " ".join(data["meta"]["caveats"]).lower()
    assert "не даёт сигналов" in text or "сигналов" in text


def test_карточки_ссылаются_на_существующие_страницы():
    """Раскладка repo: каждая ссылка плитки и документа — живой файл."""
    data = _built("repo")
    base = Path(__file__).resolve().parents[1] / "docs" / "live"
    for item in data["cards"] + data["docs"]:
        href = item["href"]
        if href.startswith("http"):
            continue
        assert (base / href).resolve().exists(), f"битая ссылка {href}"


def test_walk_forward_приписан_той_странице_где_он_считался():
    """Проверка вне выбора параметров — на восьмёрке (§8), а не на
    вселенной по дате (§9). Карточка с числами 24 монет не должна её
    обещать."""
    data = _built()
    cards = {c["title"]: c for c in data["cards"]}
    hist = cards.get("Тренд-фильтр")
    if hist is None:
        pytest.skip("нет данных тренда")
    assert "walk-forward" not in hist["what"].lower()
    eight = cards.get("Тренд на восьмёрке лидеров")
    assert eight is not None and "walk-forward" in eight["what"].lower()


def test_данные_не_теряются_при_сборке_страницы(tmp_path):
    data = _built()
    raw = build_page(_stub(tmp_path), data).replace("<\\/", "</")
    back = json.loads(raw)
    assert back["measurement"]["table"] == data["measurement"]["table"]
