# Проверка стопов, направлений и всех бумажных позиций

Исходный срез: 06.10.2026 22:07:38 МСК. Проверка после установки: 06.10.2026 22:15:41 МСК. Источники: семь SQL execution.db, журнал закрытий, локальные свечи и trend-now.json. Только бумажная торговля. Raw-журналы, ключи и учётные данные в Git не публикуются.

Коррекция расчёта: partial уже включён в close.pnl и не прибавляется повторно. В первоначальной версии managed ошибочно показан +10.0611USDT; корректный результат этого же среза −11.0671USDT. Состояние счетов и сделки не изменялись. Отдельная оценка новых входов: RULE-EVALUATION-20261006.md / https://vpn.markus.tw1.su/rule-evaluation.html.

До установки: 137 ненулевых открытых позиций, 247 закрытий, 120 выходов по стопу (48.6%). 19 открытых позиций имели исходный стоп шире 5%, 8 — шире 10%. После установки в проверенном срезе 144 позиций. Это разные тестовые профили; одинаковая монета в нескольких профилях не является независимым рыночным наблюдением.

## Что было неправильно

1. volume_splash входил самостоятельно, стоп брался за минимум/максимум большой импульсной свечи. FLUID: вход 2.207, стоп 1.857, расстояние 15.86%. Такое расстояние — не риск всего капитала; риск равен расстоянию до стопа, умноженному на qty, плюс расходы.
2. Для пробоя и смены структуры точный trigger_level не передавался по всему пути график → JSON → бот → позиция. Свежая цена могла оказаться обратно за уровнем до исполнения.
3. Выход по противоположному сигналу зависел от временного списка свежих формаций. Подтверждённая отмена старой идеи могла исчезнуть из списка, хотя позиция оставалась открытой.
4. Незаконченная текущая минута ошибочно считалась пробелом истории, когда курсор уже покрывал все закрытые минуты. Настоящие пробелы по-прежнему требуют восстановления.

## Что установлено

- Объёмный всплеск — кандидат для отдельного подтверждения пробоем/ретестом. Сам по себе он больше не открывает автоматическую позицию. Старые объёмные позиции не переименовываются задним числом.
- Для breakout/retest/structure_break сохраняется точный уровень. Перед входом и после расчёта исполнения проверяются текущая цена и свежая закрытая свеча: обе должны быть с нужной стороны уровня.
- Новые автоматические входы ограничены стопом 3% на 5м, 5% на 15м, 8% на 1ч, 12% на 4ч; при наличии истории также максимум 3 средних TR за 14 предшествующих свечей. Импульсная свеча в эту базу не входит. Это начальные ограничения бумажного испытания, их преимущество по доходности ещё не доказано. Широкий сетап пропускается; стоп не подтягивается произвольно ради входа. Явные ручные команды сохраняют заданные пользователем уровни.
- Отдельный screener-thesis.service каждые 10 секунд изучает локальные свечи открытых позиций вне блокировки исполнения. Два закрытия обратно за сохранённый уровень отменяют идею. Для старых позиций без уровня проверяется известный до выноса экстремум, возврат закрытием и слом границы свечи выноса следующей закрытой свечой. После восстановления уровня нужна новая подтверждённая потеря.
- Правило симметрично для long/short и связано с конкретной позицией/временем её открытия. Используется её собственный ТФ; младший шум не закрывает часовую позицию. Пробелы/устаревшие свечи не дают такого выхода. Исполнение требует свежей текущей цены; стоп и тейк имеют приоритет. Автоматического переворота позиции по одному стопу нет.
- Ограничения капитала/200 позиций, бумажный режим и накопленная история сохранены. Старые дальние стопы ниже перечислены отдельно: новое ограничение не меняет их задним числом. Временных выходов в новых правилах нет; старые timeout остаются в честном журнале.

## Подтверждённые выходы после установки

| Профиль | Монета | ТФ | Цена выхода | Net USDT в журнале | Причина |
| --- | --- | --- | --- | --- | --- |
| screener-alerts | FLUIDUSDT | 1h | 1.913000 | -8.617 | Подтверждённый разворот short 1h: повторная потеря границы подтверждённого ложного выноса: два закрытия за уровнем, уровень 2.034, закрытие 1.921 и текущая цена 1.9135 за уровнем |
| screener-managed | ZECUSDT | 15m | 1358.210000 | -2.252 | Подтверждённый разворот short 15m: повторная потеря границы подтверждённого ложного выноса: два закрытия за уровнем, уровень 1369.04, закрытие 1359.01 и текущая цена 1358.215 за уровнем |
| screener-all-trend-tf | QNTUSDT | 15m | 255.806272 | -6.594 | Подтверждённый разворот short 15m: повторная потеря границы подтверждённого ложного выноса: два закрытия за уровнем, уровень 265.03, закрытие 257.25 и текущая цена 255.815 за уровнем |
| screener-all-trend-tf | SKYUSDT | 15m | 0.087920 | -0.031 | Подтверждённый разворот long 15m: ложный вынос известного экстремума; следующая свеча сломала границу свечи выноса, уровень 0.08745, закрытие 0.08766 и текущая цена 0.087915 за уровнем |

Цены — реальные бумажные исполнения на момент обработки. Не использовалась историческая цена в момент появления разворота. Funding может уточняться отдельным учётом. Нельзя приписывать исправлению экономию всей уже накопленной просадки.

## История профилей до установки

| Профиль | Закрытий | Стопов | Net USDT | Все причины |
| --- | --- | --- | --- | --- |
| screener-alerts | 54 | 38 | 33.328 | stop: 38, target: 14, timeout: 2 |
| screener-alerts-structure | 11 | 9 | -0.236 | stop: 9, target: 2 |
| screener-all | 37 | 14 | -19.250 | target: 22, stop: 14, timeout: 1 |
| screener-all-trend-tf | 89 | 32 | 22.062 | target: 50, stop: 32, timeout: 7 |
| screener-can-trade | 9 | 7 | 0.758 | stop: 7, target: 2 |
| screener-managed | 42 | 16 | -11.067 | timeout: 9, stop: 16, trail: 17 |
| screener-structure-fast | 5 | 4 | -0.273 | stop: 4, target: 1 |

Частые стопы не означают автоматически убыток: alerts имел 38 стопов из 54 закрытий и положительный итог. Данные смешивают старые версии, разные размеры позиций, трейлинг и правила; сумма результатов профилей не является доходностью единого счёта. Net включает закрытый PnL, комиссии, funding и учтённые частичные закрытия. Малые выборки нельзя считать доказанным преимуществом.

## Все текущие позиции

| Профиль | Монета | ТФ | Сторона | Сетап | Вход | Стоп | Цель | Исходный стоп | ATR× | R:R исходный | Тренд сейчас | Gross USDT | Флаги |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| screener-managed | BRUSDT | 1h | long | structure_break | 0.608350 | 0.381010 | трейлинг | 37.37% | 2.90 | — | long | -0.032 | старый стоп шире нового лимита |
| screener-all-trend-tf | BRUSDT | 1h | long | structure_break | 0.590660 | 0.381010 | 0.895490 | 35.49% | 2.88 | 1.45 | long | 0.119 | старый стоп шире нового лимита |
| screener-managed | RLCUSDT | 15m | short | structure_break | 0.824000 | 0.971000 | трейлинг | 17.84% | 2.78 | — | short | 0.011 | старый стоп шире нового лимита |
| screener-all-trend-tf | PROMUSDT | 1h | short | volume_splash | 5.362000 | 6.014000 | 3.047000 | 12.16% | 2.66 | 3.55 | short | 0.070 | старый стоп шире нового лимита; старый вход только по объёму |
| screener-alerts | PARTIUSDT | 1h | long | structure_break | 0.030930 | 0.027210 | 0.041970 | 12.03% | 3.75 | 2.97 | long | 6.973 | старый стоп шире нового лимита; стоп >3 ATR в срезе аудита |
| screener-managed | LYNUSDT | 1h | short | volume_splash | 0.021880 | 0.024490 | трейлинг | 11.93% | 1.68 | — | short | 0.025 | старый стоп шире нового лимита; старый вход только по объёму |
| screener-managed | ORCAUSDT | 1h | long | volume_splash | 2.880000 | 2.566000 | трейлинг | 10.90% | 1.59 | — | long | 0.044 | старый стоп шире нового лимита; старый вход только по объёму |
| screener-all-trend-tf | ORCAUSDT | 1h | long | volume_splash | 2.848000 | 2.566000 | 4.382000 | 9.90% | 1.56 | 5.44 | long | 0.092 | старый стоп шире нового лимита; старый вход только по объёму |
| screener-all-trend-tf | 龙虾USDT | 1h | long | breakout | 0.056410 | 0.051190 | 0.066000 | 9.25% | 1.94 | 1.84 | long | 0.031 | старый стоп шире нового лимита |
| screener-managed | 龙虾USDT | 1h | long | breakout | 0.056310 | 0.051190 | трейлинг | 9.09% | 1.90 | — | long | 0.178 | старый стоп шире нового лимита |
| screener-all-trend-tf | GRIFFAINUSDT | 1h | long | volume_splash | 0.024826 | 0.022762 | 0.038198 | 8.31% | 1.59 | 6.48 | long | 0.002 | старый стоп шире нового лимита; старый вход только по объёму |
| screener-all-trend-tf | CAPUSDT | 1h | long | breakout | 0.086320 | 0.079690 | 0.127020 | 7.68% | 0.86 | 6.14 | long | -0.066 | нет перечисленных флагов |
| screener-managed | GRIFFAINUSDT | 1h | long | volume_splash | 0.024225 | 0.022762 | трейлинг | 6.04% | 1.01 | — | long | 0.141 | старый вход только по объёму |
| screener-alerts | MAGMAUSDT | 1h | long | structure_break | 0.215370 | 0.202600 | 0.263990 | 5.93% | 1.49 | 3.81 | flat | 0.015 | нет перечисленных флагов |
| screener-alerts-structure | MAGMAUSDT | 1h | long | structure_break | 0.215370 | 0.202600 | 0.263990 | 5.93% | 1.49 | 3.81 | flat | 0.014 | нет перечисленных флагов |
| screener-managed | TRBUSDT | 1h | long | volume_splash | 23.196000 | 21.854000 | трейлинг | 5.79% | 1.83 | — | long | 0.000 | старый вход только по объёму |
| screener-all-trend-tf | EDUUSDT | 15m | long | breakout | 0.068830 | 0.065330 | 0.077920 | 5.08% | 1.98 | 2.60 | long | 0.049 | старый стоп шире нового лимита |
| screener-all-trend-tf | PONSUSDT | 1h | long | breakout | 0.417300 | 0.397500 | 0.430000 | 4.74% | 1.72 | 0.64 | long | -0.000 | нет перечисленных флагов |
| screener-all-trend-tf | NIGHTUSDT | 15m | short | structure_break | 0.048400 | 0.050490 | 0.045910 | 4.32% | 3.31 | 1.19 | flat | -0.004 | стоп >3 ATR в срезе аудита |
| screener-managed | NIGHTUSDT | 15m | short | structure_break | 0.048410 | 0.050490 | трейлинг | 4.30% | 3.45 | — | flat | -0.001 | стоп >3 ATR в срезе аудита |
| screener-managed | PONSUSDT | 1h | long | breakout | 0.414400 | 0.397500 | трейлинг | 4.08% | 1.47 | — | long | -0.051 | нет перечисленных флагов |
| screener-managed | ETHFIUSDT | 1h | long | breakout | 0.773100 | 0.743800 | трейлинг | 3.79% | 1.78 | — | long | -0.009 | нет перечисленных флагов |
| screener-all-trend-tf | METUSDT | 1h | long | breakout | 0.332400 | 0.320100 | 0.344400 | 3.70% | 1.38 | 0.98 | long | 0.046 | нет перечисленных флагов |
| screener-all-trend-tf | TRBUSDT | 1h | long | volume_splash | 23.126000 | 22.282000 | 24.498000 | 3.65% | 1.45 | 1.63 | long | 0.015 | старый вход только по объёму |
| screener-can-trade | LYNUSDT | 1h | short | breakout | 0.021800 | 0.022580 | 0.019133 | 3.58% | 0.48 | 3.42 | short | 0.863 | нет перечисленных флагов |
| screener-managed | QNTUSDT | 15m | long | structure_break | 261.330000 | 252.210000 | трейлинг | 3.49% | 3.50 | — | short | -0.001 | стоп >3 ATR в срезе аудита; против текущего EMA-тренда |
| screener-all-trend-tf | WLDUSDT | 15m | short | structure_break | 0.549100 | 0.568200 | 0.530200 | 3.48% | 3.04 | 0.99 | flat | -0.005 | стоп >3 ATR в срезе аудита |
| screener-all | AAVEUSDT | 1h | short | bounce | 183.160000 | 189.366408 | 177.850000 | 3.39% | 2.35 | 0.86 | flat | 2.025 | нет перечисленных флагов |
| screener-managed | TIAUSDT | 15m | long | structure_break | 0.474100 | 0.458400 | трейлинг | 3.31% | 3.81 | — | long | 0.000 | стоп >3 ATR в срезе аудита |
| screener-managed | WLDUSDT | 15m | short | structure_break | 0.550000 | 0.568200 | трейлинг | 3.31% | 2.90 | — | flat | -0.007 | нет перечисленных флагов |
| screener-all-trend-tf | APTUSDT | 1h | long | retest | 0.841500 | 0.813678 | 0.851700 | 3.31% | 2.33 | 0.37 | long | -0.003 | нет перечисленных флагов |
| screener-all-trend-tf | TAOUSDT | 1h | short | bounce | 302.660000 | 311.938523 | 281.750000 | 3.07% | 2.78 | 2.25 | flat | -0.004 | нет перечисленных флагов |
| screener-alerts | LYNUSDT | 5m | short | breakout | 0.021170 | 0.021780 | 0.019602 | 2.88% | 2.02 | 2.57 | short | 3.104 | нет перечисленных флагов |
| screener-managed | ZROUSDT | 1h | long | retest | 2.236200 | 2.172079 | трейлинг | 2.87% | 1.18 | — | long | 0.040 | нет перечисленных флагов |
| screener-managed | ARBUSDT | 15m | long | structure_break | 0.204120 | 0.198310 | трейлинг | 2.85% | 3.28 | — | long | 0.007 | стоп >3 ATR в срезе аудита |
| screener-managed | MAGMAUSDT | 15m | short | retest | 0.215420 | 0.221521 | трейлинг | 2.83% | 2.47 | — | short | -0.013 | нет перечисленных флагов |
| screener-managed | PENGUUSDT | 15m | short | structure_break | 0.009362 | 0.009627 | трейлинг | 2.83% | 3.68 | — | short | -0.003 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | MAGMAUSDT | 15m | short | retest | 0.215430 | 0.221521 | 0.213207 | 2.83% | 2.57 | 0.36 | short | -0.013 | нет перечисленных флагов |
| screener-all-trend-tf | USUSDT | 15m | long | retest | 0.014745 | 0.014332 | 0.015376 | 2.80% | 0.93 | 1.53 | long | -0.011 | нет перечисленных флагов |
| screener-managed | SANDUSDT | 15m | long | structure_break | 0.066640 | 0.064810 | трейлинг | 2.75% | 3.42 | — | flat | -0.004 | стоп >3 ATR в срезе аудита |
| screener-managed | USUSDT | 15m | long | retest | 0.014726 | 0.014332 | трейлинг | 2.68% | 0.89 | — | long | -0.003 | нет перечисленных флагов |
| screener-all-trend-tf | GRASSUSDT | 1h | short | breakout | 0.681800 | 0.699600 | 0.679750 | 2.61% | 1.15 | 0.12 | short | -0.000 | нет перечисленных флагов |
| screener-all-trend-tf | VIRTUALUSDT | 1h | short | breakout | 0.813800 | 0.834900 | 0.805800 | 2.59% | 2.07 | 0.38 | short | 0.024 | нет перечисленных флагов |
| screener-all-trend-tf | ZROUSDT | 1h | long | retest | 2.217300 | 2.161700 | 2.374100 | 2.51% | 1.04 | 2.82 | long | 0.085 | нет перечисленных флагов |
| screener-managed | EDUUSDT | 15m | long | retest | 0.068740 | 0.067022 | трейлинг | 2.50% | 0.83 | — | long | 0.053 | нет перечисленных флагов |
| screener-all-trend-tf | ZECUSDT | 15m | short | structure_break | 1343.670000 | 1377.200000 | 1317.000000 | 2.50% | 3.23 | 0.80 | flat | -0.000 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | PUMPUSDT | 1h | short | breakout | 0.006188 | 0.006340 | 0.006096 | 2.46% | 1.22 | 0.61 | short | -0.032 | нет перечисленных флагов |
| screener-all-trend-tf | CRVUSDT | 15m | short | structure_break | 0.359500 | 0.368200 | 0.356500 | 2.42% | 3.66 | 0.34 | flat | -0.038 | стоп >3 ATR в срезе аудита |
| screener-managed | POLUSDT | 15m | short | structure_break | 0.107690 | 0.110200 | трейлинг | 2.33% | 3.57 | — | — | 0.000 | стоп >3 ATR в срезе аудита |
| screener-alerts | CTUSDT | 1h | short | retest | 0.398204 | 0.407226 | 0.342032 | 2.27% | 0.80 | 6.23 | short | 14.870 | нет перечисленных флагов |
| screener-all-trend-tf | AEROUSDT | 1h | short | volume_splash | 0.803100 | 0.820300 | 0.756600 | 2.14% | 1.37 | 2.70 | short | -0.023 | старый вход только по объёму |
| screener-all-trend-tf | PENGUUSDT | 1h | short | breakout | 0.009375 | 0.009573 | 0.009310 | 2.11% | 1.55 | 0.33 | short | 0.004 | нет перечисленных флагов |
| screener-all-trend-tf | FARTCOINUSDT | 1h | short | breakout | 0.172200 | 0.175729 | 0.170633 | 2.05% | 1.17 | 0.44 | short | 0.021 | нет перечисленных флагов |
| screener-managed | ASTERUSDT | 1h | long | retest | 0.741800 | 0.726804 | трейлинг | 2.02% | 1.81 | — | long | -0.011 | нет перечисленных флагов |
| screener-all-trend-tf | TIAUSDT | 15m | long | retest | 0.480600 | 0.471166 | 0.484800 | 1.96% | 1.76 | 0.45 | long | 0.000 | нет перечисленных флагов |
| screener-managed | C98USDT | 5m | short | breakout | 0.017960 | 0.017960 | трейлинг | 1.95% | 1.80 | — | short | 0.021 | нет перечисленных флагов |
| screener-managed | AKEUSDT | 1h | short | retest | 0.029671 | 0.030243 | трейлинг | 1.93% | 1.20 | — | short | 0.054 | нет перечисленных флагов |
| screener-managed | METUSDT | 1h | long | breakout | 0.336900 | 0.330600 | трейлинг | 1.87% | 0.61 | — | long | -0.022 | нет перечисленных флагов |
| screener-all-trend-tf | SUIUSDT | 15m | short | retest | 1.180100 | 1.201761 | 1.172867 | 1.84% | 2.38 | 0.33 | flat | -0.000 | нет перечисленных флагов |
| screener-managed | GRASSUSDT | 1h | short | breakout | 0.687000 | 0.699600 | трейлинг | 1.83% | 0.80 | — | short | -0.015 | нет перечисленных флагов |
| screener-all-trend-tf | ATOMUSDT | 1h | short | retest | 1.795000 | 1.827344 | 1.764000 | 1.80% | 1.92 | 0.96 | — | 0.023 | нет перечисленных флагов |
| screener-all-trend-tf | JUPUSDT | 15m | long | retest | 0.354900 | 0.348548 | 0.374100 | 1.79% | 1.39 | 3.02 | flat | -0.002 | нет перечисленных флагов |
| screener-managed | VVVUSDT | 1h | short | breakout | 27.369000 | 27.858673 | трейлинг | 1.79% | 1.01 | — | short | -0.004 | нет перечисленных флагов |
| screener-alerts | STRKUSDT | 1h | short | breakout | 0.051020 | 0.051900 | 0.047975 | 1.72% | 1.17 | 3.46 | short | -0.009 | нет перечисленных флагов |
| screener-alerts-structure | STRKUSDT | 1h | short | breakout | 0.051020 | 0.051900 | 0.047975 | 1.72% | 1.17 | 3.46 | short | -0.009 | нет перечисленных флагов |
| screener-managed | BOMEUSDT | 15m | short | retest | 0.000997 | 0.001014 | трейлинг | 1.66% | 1.95 | — | short | -0.015 | нет перечисленных флагов |
| screener-managed | STRKUSDT | 15m | short | retest | 0.050710 | 0.051533 | трейлинг | 1.62% | 2.18 | — | short | -0.039 | нет перечисленных флагов |
| screener-all-trend-tf | LSKUSDT | 15m | short | retest | 0.249940 | 0.253992 | 0.246470 | 1.62% | 2.07 | 0.86 | short | 0.000 | нет перечисленных флагов |
| screener-all-trend-tf | AVAXUSDT | 15m | long | retest | 11.524000 | 11.341574 | 11.691000 | 1.58% | 2.35 | 0.92 | flat | -0.029 | нет перечисленных флагов |
| screener-all-trend-tf | JTOUSDT | 15m | short | retest | 0.551300 | 0.559990 | 0.549200 | 1.58% | 1.30 | 0.24 | short | 0.010 | нет перечисленных флагов |
| screener-managed | ETHUSDT | 1h | long | bounce | 2713.920000 | 2671.545578 | трейлинг | 1.56% | 3.78 | — | short | -2.003 | стоп >3 ATR в срезе аудита; против текущего EMA-тренда |
| screener-all | 1000SHIBUSDT | 1h | short | bounce | 0.005966 | 0.006056 | 0.005492 | 1.50% | 1.40 | 5.29 | short | 6.012 | нет перечисленных флагов |
| screener-managed | VTHOUSDT | 1h | long | retest | 0.000725 | 0.000714 | трейлинг | 1.49% | 0.44 | — | flat | -0.011 | нет перечисленных флагов |
| screener-managed | BTCUSDT | 1h | long | retest | 86238.000000 | 84955.218839 | трейлинг | 1.49% | 3.67 | — | short | -0.000 | стоп >3 ATR в срезе аудита; против текущего EMA-тренда |
| screener-all-trend-tf | ENAUSDT | 1h | short | breakout | 0.235650 | 0.239140 | 0.233660 | 1.48% | 1.00 | 0.57 | short | -0.044 | нет перечисленных флагов |
| screener-all-trend-tf | POLUSDT | 1h | short | breakout | 0.107070 | 0.108643 | 0.105475 | 1.47% | 1.26 | 1.01 | — | -0.000 | нет перечисленных флагов |
| screener-all-trend-tf | VVVUSDT | 1h | short | breakout | 27.478000 | 27.858673 | 27.178000 | 1.39% | 0.76 | 0.79 | short | 0.015 | нет перечисленных флагов |
| screener-all-trend-tf | STRKUSDT | 15m | short | breakout | 0.050970 | 0.051660 | 0.049580 | 1.35% | 1.74 | 2.01 | short | -0.014 | нет перечисленных флагов |
| screener-managed | FETUSDT | 15m | short | retest | 0.241400 | 0.244637 | трейлинг | 1.34% | 1.90 | — | short | 0.002 | нет перечисленных флагов |
| screener-managed | JTOUSDT | 15m | short | retest | 0.552600 | 0.559990 | трейлинг | 1.34% | 1.11 | — | short | 0.021 | нет перечисленных флагов |
| screener-all-trend-tf | MUBARAKUSDT | 5m | long | retest | 0.076480 | 0.075474 | 0.076620 | 1.32% | 2.74 | 0.14 | long | -0.000 | нет перечисленных флагов |
| screener-managed | LINKUSDT | 15m | short | retest | 13.925000 | 14.107132 | трейлинг | 1.31% | 2.54 | — | flat | -0.000 | нет перечисленных флагов |
| screener-all | BTCUSDT | 1h | long | bounce | 85682.000000 | 84589.832636 | 86969.866667 | 1.27% | 3.58 | 1.18 | short | -0.462 | стоп >3 ATR в срезе аудита; против текущего EMA-тренда |
| screener-all-trend-tf | LTCUSDT | 1h | short | breakout | 68.980000 | 69.833147 | 67.965000 | 1.24% | 1.96 | 1.19 | short | -0.006 | нет перечисленных флагов |
| screener-managed | PAXGUSDT | 1h | long | bounce | 4168.620000 | 4117.365787 | трейлинг | 1.23% | 3.46 | — | long | 0.002 | стоп >3 ATR в срезе аудита |
| screener-managed | SUIUSDT | 15m | short | retest | 1.187400 | 1.201761 | трейлинг | 1.21% | 2.35 | — | flat | 0.015 | нет перечисленных флагов |
| screener-managed | XLMUSDT | 15m | short | retest | 0.213190 | 0.215763 | трейлинг | 1.21% | 3.24 | — | short | 0.008 | стоп >3 ATR в срезе аудита |
| screener-managed | HYPEUSDT | 15m | short | retest | 91.819000 | 92.922270 | трейлинг | 1.20% | 2.62 | — | flat | 0.012 | нет перечисленных флагов |
| screener-managed | BNBUSDT | 15m | short | retest | 781.070000 | 790.380311 | трейлинг | 1.19% | 5.90 | — | short | 0.003 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | XAUTUSDT | 1h | long | bounce | 4158.720000 | 4109.310028 | 4209.570000 | 1.19% | 3.43 | 1.03 | long | 0.175 | стоп >3 ATR в срезе аудита |
| screener-managed | UNIUSDT | 15m | short | retest | 8.781000 | 8.735795 | трейлинг | 1.18% | 2.15 | — | short | 0.000 | нет перечисленных флагов |
| screener-managed | DOGEUSDT | 15m | short | retest | 0.094140 | 0.095229 | трейлинг | 1.16% | 2.88 | — | short | 0.013 | нет перечисленных флагов |
| screener-managed | 牛来USDT | 15m | long | retest | 0.079810 | 0.078911 | трейлинг | 1.13% | 1.07 | — | long | -0.032 | нет перечисленных флагов |
| screener-all-trend-tf | VTHOUSDT | 1h | long | retest | 0.000722 | 0.000714 | 0.000778 | 1.12% | 0.31 | 6.93 | flat | 0.000 | нет перечисленных флагов |
| screener-managed | LDOUSDT | 15m | long | retest | 0.471800 | 0.466526 | трейлинг | 1.12% | 1.22 | — | long | -0.021 | нет перечисленных флагов |
| screener-all-trend-tf | XLMUSDT | 15m | short | retest | 0.213430 | 0.215763 | 0.210610 | 1.09% | 2.14 | 1.21 | short | 0.000 | нет перечисленных флагов |
| screener-all-trend-tf | FETUSDT | 15m | short | retest | 0.242000 | 0.244637 | 0.227500 | 1.09% | 1.44 | 5.50 | short | 0.001 | нет перечисленных флагов |
| screener-all-trend-tf | BNBUSDT | 15m | short | retest | 781.930000 | 790.380311 | 765.245000 | 1.08% | 4.99 | 1.97 | short | 0.356 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | FILUSDT | 1h | long | breakout | 1.162900 | 1.150525 | 1.327600 | 1.06% | 0.64 | 13.31 | flat | -0.000 | нет перечисленных флагов |
| screener-managed | FLUIDUSDT | 15m | short | retest | 1.921000 | 1.941406 | трейлинг | 1.06% | 0.91 | — | short | 0.026 | нет перечисленных флагов |
| screener-all-trend-tf | DASHUSDT | 5m | short | retest | 56.280000 | 56.874853 | 55.960000 | 1.06% | 3.98 | 0.54 | short | -0.000 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | XPLUSDT | 1h | short | breakout | 0.090470 | 0.091420 | 0.089070 | 1.05% | 0.84 | 1.47 | short | -0.023 | нет перечисленных флагов |
| screener-all-trend-tf | PAXGUSDT | 1h | long | bounce | 4160.730000 | 4117.365787 | 4215.983333 | 1.04% | 3.06 | 1.27 | long | 0.000 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | TRXUSDT | 1h | long | bounce | 0.336200 | 0.332826 | 0.339267 | 1.00% | 5.05 | 0.91 | flat | -0.309 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | USELESSUSDT | 5m | short | breakout | 0.224810 | 0.226970 | 0.222575 | 0.96% | 1.67 | 1.03 | short | 0.009 | нет перечисленных флагов |
| screener-managed | USELESSUSDT | 5m | short | breakout | 0.224810 | 0.226970 | трейлинг | 0.96% | 1.67 | — | short | 0.009 | нет перечисленных флагов |
| screener-all-trend-tf | ETHFIUSDT | 15m | long | breakout | 0.774400 | 0.767040 | 0.777000 | 0.95% | 0.98 | 0.35 | long | -0.000 | нет перечисленных флагов |
| screener-managed | APTUSDT | 15m | long | retest | 0.836200 | 0.828276 | трейлинг | 0.95% | 1.13 | — | long | 0.002 | нет перечисленных флагов |
| screener-all-trend-tf | BCHUSDT | 1h | short | breakout | 314.690000 | 317.602986 | 313.870000 | 0.93% | 1.57 | 0.28 | flat | -0.002 | нет перечисленных флагов |
| screener-managed | HBARUSDT | 15m | long | bounce | 0.100650 | 0.099747 | трейлинг | 0.90% | 2.09 | — | long | 0.002 | нет перечисленных флагов |
| screener-all-trend-tf | 牛来USDT | 15m | long | retest | 0.079600 | 0.078911 | 0.080454 | 0.87% | 0.82 | 1.24 | long | -0.019 | нет перечисленных флагов |
| screener-managed | TRXUSDT | 1h | long | bounce | 0.336050 | 0.333153 | трейлинг | 0.86% | 4.49 | — | flat | -0.217 | стоп >3 ATR в срезе аудита |
| screener-managed | INJUSDT | 1h | long | breakout | 7.833000 | 7.766000 | трейлинг | 0.86% | 0.61 | — | long | 0.001 | нет перечисленных флагов |
| screener-all-trend-tf | AAVEUSDT | 5m | long | retest | 182.500000 | 180.944248 | 183.460000 | 0.85% | 2.74 | 0.62 | long | -0.002 | нет перечисленных флагов |
| screener-all-trend-tf | OPUSDT | 5m | short | retest | 0.131460 | 0.132566 | 0.130910 | 0.84% | 2.11 | 0.50 | flat | -0.010 | нет перечисленных флагов |
| screener-all-trend-tf | TRUMPUSDT | 1h | short | breakout | 2.012000 | 2.028758 | 1.994000 | 0.83% | 0.85 | 1.07 | short | 0.008 | нет перечисленных флагов |
| screener-all-trend-tf | HYPEUSDT | 5m | short | retest | 91.961000 | 92.719167 | 91.468000 | 0.82% | 3.66 | 0.65 | flat | 0.000 | стоп >3 ATR в срезе аудита |
| screener-all | TRXUSDT | 1h | long | bounce | 0.334610 | 0.331854 | 0.339267 | 0.82% | 5.65 | 1.69 | flat | 0.641 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | LDOUSDT | 15m | long | retest | 0.470400 | 0.466526 | 0.474000 | 0.82% | 0.89 | 0.93 | long | -0.006 | нет перечисленных флагов |
| screener-managed | MUBARAKUSDT | 15m | long | breakout | 0.076280 | 0.075655 | трейлинг | 0.82% | 0.83 | — | long | -0.015 | нет перечисленных флагов |
| screener-managed | FILUSDT | 1h | long | breakout | 1.160000 | 1.150525 | трейлинг | 0.82% | 0.49 | — | flat | -0.022 | нет перечисленных флагов |
| screener-managed | PUMPUSDT | 5m | short | retest | 0.006219 | 0.006268 | трейлинг | 0.79% | 1.69 | — | short | -0.006 | нет перечисленных флагов |
| screener-alerts-structure | FLUIDUSDT | 15m | short | breakout | 1.927000 | 1.942000 | 1.864672 | 0.78% | 0.66 | 4.16 | short | 0.041 | нет перечисленных флагов |
| screener-all-trend-tf | FLUIDUSDT | 15m | short | retest | 1.927000 | 1.942000 | 1.801000 | 0.78% | 0.66 | 8.40 | short | 0.035 | нет перечисленных флагов |
| screener-all-trend-tf | ICPUSDT | 5m | short | retest | 3.422000 | 3.446839 | 3.404000 | 0.73% | 3.02 | 0.72 | short | -0.000 | стоп >3 ATR в срезе аудита |
| screener-managed | FARTCOINUSDT | 5m | short | breakout | 0.171500 | 0.172744 | трейлинг | 0.73% | 1.91 | — | short | -0.000 | нет перечисленных флагов |
| screener-managed | UMAUSDT | 5m | short | breakout | 0.436000 | 0.436000 | трейлинг | 0.71% | 1.59 | — | short | 0.008 | нет перечисленных флагов |
| screener-all-trend-tf | ETHUSDT | 1h | long | bounce | 2701.590000 | 2682.557761 | 2779.000000 | 0.70% | 1.55 | 4.07 | short | -0.001 | против текущего EMA-тренда |
| screener-alerts-structure | FLUIDUSDT | 5m | short | breakout | 1.921000 | 1.934328 | 1.870656 | 0.69% | 1.88 | 3.78 | short | 0.026 | нет перечисленных флагов |
| screener-all-trend-tf | INJUSDT | 1h | long | breakout | 7.820000 | 7.766000 | 7.925000 | 0.69% | 0.50 | 1.94 | long | 0.000 | нет перечисленных флагов |
| screener-all-trend-tf | RAYSOLUSDT | 1h | long | volume_splash | 2.204800 | 2.189600 | 2.557300 | 0.69% | 0.38 | 23.19 | long | -0.016 | старый вход только по объёму |
| screener-managed | ICPUSDT | 15m | short | retest | 3.428000 | 3.451277 | трейлинг | 0.68% | 1.23 | — | short | 0.009 | нет перечисленных флагов |
| screener-all-trend-tf | ADAUSDT | 5m | short | retest | 0.271400 | 0.273176 | 0.270100 | 0.65% | 2.18 | 0.73 | short | 0.009 | нет перечисленных флагов |
| screener-all-trend-tf | BOMEUSDT | 5m | short | retest | 0.001010 | 0.001017 | 0.000996 | 0.65% | 1.74 | 2.22 | short | 0.001 | нет перечисленных флагов |
| screener-managed | SOLUSDT | 5m | long | retest | 120.750000 | 119.975008 | трейлинг | 0.64% | 3.50 | — | long | -0.004 | стоп >3 ATR в срезе аудита |
| screener-all | PAXGUSDT | 1h | long | bounce | 4136.770000 | 4111.304126 | 4215.983333 | 0.62% | 3.83 | 3.11 | long | 2.103 | стоп >3 ATR в срезе аудита |
| screener-all-trend-tf | NEARUSDT | 5m | short | retest | 5.061000 | 5.088908 | 5.003000 | 0.55% | 1.43 | 2.08 | flat | -0.003 | нет перечисленных флагов |
| screener-managed | LTCUSDT | 1h | short | breakout | 69.460000 | 69.833147 | трейлинг | 0.54% | 0.83 | — | short | -0.010 | нет перечисленных флагов |
| screener-managed | XMRUSDT | 5m | short | retest | 559.220000 | 561.959300 | трейлинг | 0.49% | 2.21 | — | short | -0.001 | нет перечисленных флагов |
| screener-managed | VIRTUALUSDT | 5m | short | breakout | 0.811000 | 0.814808 | трейлинг | 0.47% | 2.01 | — | short | 0.007 | нет перечисленных флагов |
| screener-all-trend-tf | SOLUSDT | 5m | short | bounce | 121.180000 | 121.734079 | 118.730000 | 0.46% | 2.49 | 4.42 | long | 0.022 | против текущего EMA-тренда |
| screener-all-trend-tf | BTCUSDT | 5m | short | retest | 85476.700000 | 85847.764662 | 85072.700000 | 0.43% | 4.45 | 1.09 | short | -0.000 | стоп >3 ATR в срезе аудита |
| screener-managed | DASHUSDT | 5m | short | breakout | 56.230000 | 56.473123 | трейлинг | 0.43% | 1.70 | — | short | -0.009 | нет перечисленных флагов |
| screener-all-trend-tf | MARSCOINUSDT | 15m | long | breakout | 0.111790 | 0.111370 | 0.115155 | 0.38% | 0.17 | 8.01 | long | 0.035 | нет перечисленных флагов |

ATR× в таблице — отдельный диагностический срез перед фактическим входом; он может включать импульс и отличается от новой доимпульсной базы для допуска. Тренд 5м берётся из доступного 15м EMA-контекста; это не утверждение, что есть независимый 5м тренд. Gross открытой позиции не учитывает комиссию выхода/funding. MFE/MAE в raw-аудите рассчитаны только по полностью последующим закрытым свечам: внутрисвечный путь и частичная свеча входа не восстановлены. Входной measured_r — агрегат класса по прошлой выборке, а не прогноз отдельной монеты.

## История по сетапам, направлениям и ТФ

| Профиль | Сетап | ТФ | Сторона | N | Стопов | Net USDT | Profit factor |
| --- | --- | --- | --- | --- | --- | --- | --- |
| screener-alerts | volume_splash | 15m | long | 4 | 3 | -17.180 | 0.092 |
| screener-all | bounce | 1h | long | 3 | 2 | -13.446 | 0.100 |
| screener-alerts | volume_splash | 1h | short | 1 | 1 | -10.253 | 0.000 |
| screener-alerts | breakout | 15m | short | 11 | 8 | -9.108 | 0.604 |
| screener-managed | bounce | 1h | short | 3 | 3 | -6.566 | 0.000 |
| screener-alerts | breakout | 1h | short | 1 | 1 | -6.136 | 0.000 |
| screener-managed | structure_break | 15m | short | 7 | 3 | -6.087 | 0.564 |
| screener-managed | structure_break | 15m | long | 10 | 3 | -5.176 | 0.809 |
| screener-alerts | breakout | 1h | long | 1 | 1 | -4.747 | 0.000 |
| screener-all | bounce | 1h | short | 5 | 3 | -3.993 | 0.639 |
| screener-all | trendline_bounce | 5m | short | 4 | 4 | -3.530 | 0.000 |
| screener-alerts | retest | 5m | short | 2 | 2 | -3.282 | 0.000 |
| screener-all | trendline_bounce | 15m | short | 9 | 5 | -3.023 | 0.371 |
| screener-alerts-structure | volume_splash | 1h | long | 2 | 2 | -2.983 | 0.000 |
| screener-all-trend-tf | bounce | 5m | short | 2 | 2 | -2.653 | 0.000 |
| screener-alerts | bounce | 5m | short | 2 | 2 | -2.464 | 0.000 |
| screener-all-trend-tf | trendline_bounce | 5m | short | 5 | 3 | -2.167 | 0.001 |
| screener-all-trend-tf | bounce | 1h | short | 3 | 1 | -1.960 | 0.335 |
| screener-structure-fast | breakout | 15m | long | 2 | 2 | -1.734 | 0.000 |
| screener-managed | bounce | 5m | long | 2 | 1 | -1.615 | 0.000 |
| screener-alerts | volume_splash | 5m | short | 2 | 1 | -1.607 | 0.259 |
| screener-alerts | breakout | 5m | long | 6 | 5 | -1.578 | 0.712 |
| screener-alerts | volume_splash | 5m | long | 1 | 1 | -1.252 | 0.000 |
| screener-alerts | alert | 15m | long | 1 | 1 | -1.085 | 0.000 |
| screener-alerts | absorption | 5m | short | 1 | 1 | -0.909 | 0.000 |
| screener-all-trend-tf | retest | 15m | long | 4 | 2 | -0.894 | 0.646 |
| screener-alerts | bounce | 5m | long | 1 | 1 | -0.870 | 0.000 |
| screener-all-trend-tf | trendline_bounce | 15m | short | 2 | 1 | -0.843 | 0.118 |
| screener-all-trend-tf | liquidity_sweep | 15m | long | 1 | 0 | -0.649 | 0.000 |
| screener-all-trend-tf | volume_splash | 15m | long | 1 | 1 | -0.390 | 0.000 |
| screener-can-trade | breakout | 15m | long | 3 | 3 | -0.385 | 0.000 |
| screener-alerts | alert | 5m | short | 1 | 1 | -0.380 | 0.000 |
| screener-all-trend-tf | trendline_bounce | 5m | long | 8 | 1 | -0.359 | 0.658 |
| screener-alerts-structure | breakout | 5m | long | 1 | 1 | -0.320 | 0.000 |
| screener-structure-fast | retest | 15m | long | 1 | 1 | -0.300 | 0.000 |
| screener-alerts-structure | alert | 5m | short | 1 | 1 | -0.222 | 0.000 |
| screener-alerts-structure | retest | 5m | long | 1 | 1 | -0.175 | 0.000 |
| screener-alerts-structure | volume_splash | 15m | long | 1 | 1 | -0.136 | 0.000 |
| screener-all-trend-tf | retest | 5m | long | 5 | 4 | -0.111 | 0.000 |
| screener-can-trade | volume_splash | 15m | long | 1 | 1 | -0.097 | 0.000 |
| screener-structure-fast | volume_splash | 15m | long | 1 | 1 | -0.097 | 0.000 |
| screener-all-trend-tf | breakout | 15m | short | 2 | 2 | -0.092 | 0.000 |
| screener-all-trend-tf | retest | 5m | short | 13 | 4 | -0.072 | 0.577 |
| screener-can-trade | retest | 15m | long | 1 | 1 | -0.060 | 0.000 |
| screener-all-trend-tf | breakout | 15m | long | 1 | 1 | -0.048 | 0.000 |
| screener-all-trend-tf | breakout | 5m | long | 2 | 1 | -0.036 | 0.040 |
| screener-alerts-structure | volume_splash | 5m | short | 2 | 2 | -0.020 | 0.000 |
| screener-can-trade | breakout | 1h | short | 1 | 1 | -0.013 | 0.000 |
| screener-managed | retest | 5m | short | 1 | 1 | -0.005 | 0.000 |
| screener-managed | retest | 5m | long | 1 | 1 | -0.004 | 0.000 |
| screener-managed | retest | 15m | long | 4 | 1 | -0.002 | 0.658 |
| screener-all-trend-tf | bounce | 5m | long | 1 | 1 | -0.001 | 0.000 |
| screener-all-trend-tf | retest | 15m | short | 5 | 2 | 0.004 | 2.075 |
| screener-managed | absorption | 15m | long | 2 | 1 | 0.011 | 6.644 |
| screener-all-trend-tf | breakout | 1h | long | 1 | 0 | 0.024 | — |
| screener-all-trend-tf | breakout | 1h | short | 1 | 0 | 0.024 | — |
| screener-all-trend-tf | volume_splash | 1h | long | 1 | 0 | 0.069 | — |
| screener-alerts | absorption | 5m | long | 2 | 1 | 0.154 | 1.272 |
| screener-all-trend-tf | trendline_bounce | 15m | long | 6 | 0 | 0.175 | 1.402 |
| screener-all-trend-tf | retest | 1h | long | 1 | 0 | 0.189 | — |
| screener-all-trend-tf | absorption | 15m | short | 1 | 0 | 0.264 | — |
| screener-can-trade | breakout | 5m | long | 1 | 0 | 0.372 | — |
| screener-managed | trendline_bounce | 5m | long | 1 | 0 | 0.387 | — |
| screener-alerts | retest | 5m | long | 5 | 4 | 0.489 | 1.120 |
| screener-all | trendline_bounce | 15m | long | 15 | 0 | 0.716 | 1.492 |
| screener-managed | trendline_bounce | 15m | long | 3 | 0 | 0.778 | 3.414 |
| screener-managed | bounce | 15m | long | 1 | 0 | 0.783 | — |
| screener-can-trade | volume_splash | 1h | long | 2 | 1 | 0.942 | 81.190 |
| screener-all-trend-tf | absorption | 15m | long | 1 | 0 | 1.083 | — |
| screener-managed | bounce | 5m | short | 4 | 2 | 1.413 | 1.939 |
| screener-structure-fast | breakout | 5m | long | 1 | 0 | 1.858 | — |
| screener-managed | bounce | 1h | long | 1 | 0 | 2.158 | — |
| screener-alerts | breakout | 5m | short | 4 | 1 | 2.631 | 2.249 |
| screener-managed | retest | 15m | short | 2 | 0 | 2.857 | — |
| screener-all-trend-tf | bounce | 1h | long | 1 | 0 | 2.863 | — |
| screener-alerts-structure | breakout | 5m | short | 3 | 1 | 3.620 | 5.326 |
| screener-all | structure_break | 15m | long | 1 | 0 | 4.026 | — |
| screener-alerts | alert | 5m | long | 2 | 1 | 5.055 | 4.894 |
| screener-all-trend-tf | structure_break | 15m | short | 8 | 3 | 6.722 | 1.444 |
| screener-alerts | retest | 1h | short | 1 | 0 | 7.804 | — |
| screener-alerts | breakout | 15m | long | 1 | 0 | 9.831 | — |
| screener-all-trend-tf | structure_break | 15m | long | 13 | 3 | 20.918 | 1.650 |
| screener-alerts | volume_splash | 1h | long | 3 | 2 | 31.694 | 8.357 |
| screener-alerts | retest | 15m | long | 1 | 0 | 36.523 | — |


## Проверка и дальнейшая оценка

Локально 162 проверки торгового контура и 8 проверок потоков данных. На VPS 122 целевые проверки исполнения/подтверждения/восстановления и 8 потоковых. Сервисы после установки активны; журнал подтверждает четыре выхода по invalidation. В контрольной проверке 22:17 МСК 18 новых открытий после установки: ни одного volume_splash, превышения новых ограничений стопа или структурного входа без уровня. Recovery errors=[] после нескольких минутных границ; монитор ok, цикл около 2–3 секунд. SQL outbox подтверждает доставку четырёх фотографий выходов и отчёта в Telegram. HTTPS отчёта, ботов и структур проверен, ответы 200; визуальная проверка браузером не подтверждена из-за ранее установленной блокировки браузера.

Полезность новых правил нужно оценивать отдельно на новых сделках: доля стопов, net в R после расходов, MFE/MAE, запаздывание входа, пропуски и просадка. Сравнение по ТФ/сетапам и тренду, с отдельными часами сессий, должно идти на отложенной выборке. Не подгонять правила под FLUID и не повышать риск ради меньшего числа стопов.

Бэкап перед установкой: /opt/stop-review-backup-20261006T190737Z. В него сохранены изменяемые исходники и согласованные SQLite backup семи профилей. При отказе проверки скрипт восстанавливает код; историю торговли он не подменяет. Фактический статус свечей и уведомлений дополнен в deployment/HANDOFF.md.

## Материалы для проверки гипотез

[Binance: breakout](https://www.binance.com/en/academy/glossary/breakout): выход за уровень требует проверки, фитиль/объём сами по себе не гарантируют продолжение. [TradingView: ATR](https://www.tradingview.com/support/solutions/43000501823-average-true-range-atr/): TR учитывает диапазон и отклонение от предыдущего закрытия; ATR измеряет волатильность, не направление. В нашей контрольной базе применяется среднее TR, тогда как стандартный ATR TradingView использует RMA. [Tiger: scalping guide](https://blog.tiger.com/trading/scalping-trade-guide): учитывать препятствия, комиссии и зависимость размера позиции от расстояния до стопа; вести журнал с контекстом. Это источники гипотез, а не подтверждение прибыльности нашего бота.
