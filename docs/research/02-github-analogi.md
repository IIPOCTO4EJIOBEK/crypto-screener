# Обзор открытых проектов — аналоги для ИИ-скринера криптобиржи

Дата сбора: 29.09.2026. Источники: GitHub Search API (звёзды/лицензия/язык — на дату сбора),
локальный веб-поиск. Проект: `crypto-exchange` (скринер формаций + плотность стакана + RAG/LLM-предложения).

**Как читать вердикт:**
- **брать целиком** — готовый компонент, ставим и используем;
- **взять идею** — подход/алгоритм/архитектура полезны, код берём частично или переписываем;
- **не подходит** — мимо по языку, домену или качеству.

**Про лицензии.** `NOASSERTION` = GitHub не распознал стандартную лицензию; трактуем как
«неясные права, смотреть файл LICENSE вручную». `GPL-3.0` / `AGPL-3.0` — копилефт: для
**приватного личного** проекта это не проблема, но если когда-нибудь будем распространять
сервис или отдавать APK/веб — придётся раскрывать исходники или не линковать такой код.

---

## 0. Главный вывод (чтобы не изобретать заново)

**Ни один открытый проект не делает то же самое целиком.** Задача «плотность стакана +
формации + алерты + LLM-объяснение входа» на GitHub не собрана. Ближе всего по замыслу —
мелкие (5–194★) сканеры, но у каждого закрыт только один из трёх кубиков.
Собирать придётся самим, но **каждый кубик уже есть** в готовом виде — см. раздел 6
«Готовые кубики для сборки». Это и есть главная экономия: не писать с нуля `ccxt`-обвязку,
детектор уровней, бэктест и вебсокет-фиды.

Отдельно: **открытого аналога Tiger.com Broker не существует.** Это проприетарный
брокер-агрегатор (свой API, кешбэк-модель, доступ к Binance/Bybit/OKX из РФ). Открытые
проекты дают только куски — клиенты к Hyperliquid, агрегаторы цен CEX. См. раздел 5.

---

## 1. Скринеры и сканеры с детекцией формаций

| Проект | Ссылка | ★ | Лицензия | Язык | Что даёт | Вердикт |
|---|---|---:|---|---|---|---|
| keithorange/CryptoSuperScreener | https://github.com/keithorange/CryptoSuperScreener | 54 | none | HTML/JS | Скринер по всем биржам CCXT: фильтры, grid-watch, «лучше TradingView». Формаций нет | взять идею |
| AIUngated/crypto-liquidity-terminal | https://github.com/AIUngated/crypto-liquidity-terminal | 5 | MIT | TypeScript | Скринер + **heatmap плотности стакана Binance**, мультибиржа (Binance/Bybit/OKX). Ближе всех к нашему объёму | взять идею |
| nssanta/quant-order-book | https://github.com/nssanta/quant-order-book | 25 | none | JS | Real-time heatmap стакана + квант-метрики: индекс α, CVD, delta. Мультибиржа | взять идею |
| prado150aaa-code/crypto-screener-manus1 | https://github.com/prado150aaa-code/crypto-screener-manus1 | 0 | none | TypeScript | Real-time скринер Binance/Bybit/OKX | взять идею |
| bosskuh6666/harmonic-scanner-ai | https://github.com/bosskuh6666/harmonic-scanner-ai | 2 | none | Python | Гармонические паттерны + volume spike, мульти-таймфрейм, крипта и форекс | взять идею |
| KshitijMishra1418/crypto-market-analysis | https://github.com/KshitijMishra1418/crypto-market-analysis | 1 | none | Python | Кластеризация волатильности, детекция пробоев, скоринг ликвидности | взять идею |
| NadirAliOfficial/trading-scanner | https://github.com/NadirAliOfficial/trading-scanner | 14 | NOASSERTION | Python | Сканер **BOS/FVG/liquidity sweep**, скоринг сетапа, Telegram-алерты, Streamlit. Но MT5, не крипта | взять идею |
| bdalrhmnalslyhy704-jpg/RadarX-AI | https://github.com/bdalrhmnalslyhy704-jpg/RadarX-AI | 0 | none | HTML | ИИ-сканер «pre-breakout» (маркетинговое описание, кода мало) | не подходит |
| jaturapornchai/crypto-scanner-ai-advisor | https://github.com/jaturapornchai/crypto-scanner-ai-advisor | 0 | none | Python | Скринер + «ИИ-советник» с пробоями и Фибоначчи | не подходит |

**Вывод по разделу.** Зрелого открытого скринера формаций по крипте нет. Взять стоит
**каркас фильтрации + grid-watch** у CryptoSuperScreener (он на CCXT — совпадает с нашей
идеей «единый интерфейс, источник подменяем») и **схему скоринга сетапа + алертов** у
trading-scanner. Сами формации (breakout/retest/spring/bounce) пишем у себя —
готовых качественных детекторов именно этих формаций на GitHub не нашлось.

---

## 2. Плотность стакана, order flow, spoofing/iceberg

| Проект | Ссылка | ★ | Лицензия | Язык | Что даёт | Вердикт |
|---|---|---:|---|---|---|---|
| flowsurface-rs/flowsurface | https://github.com/flowsurface-rs/flowsurface | 1748 | GPL-3.0 | Rust | **Эталон.** Десктоп-терминал: heatmap, footprint, ladder, time&sales, Binance/Bybit/Hyperliquid/OKX, real-time агрегация | взять идею |
| DegenSugarBoo/OpenBook | https://github.com/DegenSugarBoo/OpenBook | 183 | MIT | Rust | Real-time depth heatmap фьючерсов + order flow + trade tape (egui) | взять идею |
| phil8192/ob-analytics | https://github.com/phil8192/ob-analytics | 163 | NOASSERTION | R | Реконструкция и визуализация LOB, микроструктура | взять идею |
| Azhagesan-dev/OrderFlowMap | https://github.com/Azhagesan-dev/OrderFlowMap | 81 | MIT | HTML | Bookmap-style в **одном HTML**: heatmaps, trade bubbles, DOM ladder | взять идею |
| mahmoud20138/OrderFlow-Analysis-Pro | https://github.com/mahmoud20138/OrderFlow-Analysis-Pro | 59 | MIT | Python | **Footprint, delta, volume profile, детекция паттернов.** Фиды Bybit + MT5 | взять идею |
| bigmacman1129/crypto-ai-trading-bot | https://github.com/bigmacman1129/crypto-ai-trading-bot | 194 | none | JS | Анализ стакана, **кластеры стопов, liquidity sweep**, бот | взять идею |
| nazmiefearmutcu/flowmap | https://github.com/nazmiefearmutcu/flowmap | 17 | Apache-2.0 | TS | WebGL2 liquidity heatmap, DOM ladder, time&sales | взять идею |
| Niketion/flowdepth | https://github.com/Niketion/flowdepth | 9 | GPL-3.0 | Rust | Нативный чарт-терминал для крипты | взять идею |
| mczielinski/ob-analytics | https://github.com/mczielinski/ob-analytics | 3 | NOASSERTION | Python | Python-порт ob-analytics (LOB-аналитика) | взять идею |
| iAnjaneySingh/ultra-low-latency-lob-engine | https://github.com/iAnjaneySingh/ultra-low-latency-lob-engine | 0 | none | C++ | LOB-движок + OBI, VPIN, детекция айсбергов | взять идею |
| sohamsssssssssssssssss/alphacore | https://github.com/sohamsssssssssssssssss/alphacore | 0 | MIT | Makefile | **Детекция айсбергов, spoof-алерты**, flow-анализ (NSE) | взять идею |
| luckyx7777/pump-dump-screener | https://github.com/luckyx7777/pump-dump-screener | 0 | none | Python | Раннее детектирование pump&dump по **микроструктуре стакана** Binance/Bybit | взять идею |
| yuvrajsingh1097/LIQUIDITY-SWEEP-HEATMAP | https://github.com/yuvrajsingh1097/LIQUIDITY-SWEEP-HEATMAP | 1 | MIT | Python | Визуализация liquidity sweep (Inner Circle Trader) | взять идею |
| pranagr1812-eng/Institutional-Liquidity-Heatmap | https://github.com/pranagr1812-eng/Institutional-Liquidity-Heatmap | 1 | none | Jupyter | Liquidity heatmap + KDE-структура рынка | взять идею |
| paulfav/high_frequency_spoofing_detection | https://github.com/paulfav/high_frequency_spoofing_detection | 2 | none | Jupyter | Исследование по детекции спуфинга | взять идею |
| Bosk00/market-surveillance-toolkit | https://github.com/Bosk00/market-surveillance-toolkit | 0 | none | Python | Real-time наблюдение: **спуфинг, entity-level стены** | взять идею |
| callump9523/Spoofing-Detection-Demo | https://github.com/callump9523/Spoofing-Detection-Demo | 0 | none | Jupyter | Синтетический детектор спуфинга (pandas) | взять идею |
| Vlastimir0500/hawkes-ofi-market-microstructure | https://github.com/Vlastimir0500/hawkes-ofi-market-microstructure | 2 | none | Python | Hawkes-процессы + order-flow imbalance (OFI) | взять идею |
| aurelien-gentile/orderbook-imbalance-alpha | https://github.com/aurelien-gentile/orderbook-imbalance-alpha | 1 | none | Jupyter | OBI/MLOFI/delta на крипто-перпах | взять идею |
| kostyafarber/crypto-lob-data-pipeline | https://github.com/kostyafarber/crypto-lob-data-pipeline | 25 | MIT | Python | Real-time LOB-пайплайн через Kafka (Deribit v2) | взять идею |
| thewitcher745/heatmap-bot | https://github.com/thewitcher745/heatmap-bot | 1 | none | Python | Telegram-бот рисует heatmap стакана | не подходит |

**Вывод по разделу.** Это самая зрелая область: **метрики плотности и визуализацию**
берём концептуально у `flowsurface` (архитектура real-time агрегации стакана —
лучший ориентир в опенсорсе) и `OrderFlow-Analysis-Pro` (готовый Python: footprint, delta,
volume profile — прямо ложится в наш `src/analysis/`). **Детекторы манипуляций**
(спуфинг, айсберг, стены) в опенсорсе — только исследовательские прототипы по 0–2★;
берём идеи метрик (VPIN, OBI, entity-level стены), код пишем свой. **Готового
«heatmap + spoof-алерт на крипте» в одном флаконе нет.**

---

## 3. RAG + LLM ассистенты и агенты принятия решений

| Проект | Ссылка | ★ | Лицензия | Язык | Что даёт | Вердикт |
|---|---|---:|---|---|---|---|
| TauricResearch/TradingAgents | https://github.com/TauricResearch/TradingAgents | 109176 | Apache-2.0 | Python | **Эталон мультиагентного LLM-трейдинга:** аналитики, дебаты, риск-менеджер | взять идею |
| HKUDS/Vibe-Trading | https://github.com/HKUDS/Vibe-Trading | 34281 | MIT | Python | Персональный торговый агент | взять идею |
| OpenBB-finance/OpenBB | https://github.com/OpenBB-finance/OpenBB | 73622 | NOASSERTION | Python | Открытая платформа данных для аналитиков/квантов/**ИИ-агентов**, единый слой данных | взять идею |
| ginlix-ai/LangAlpha | https://github.com/ginlix-ai/LangAlpha | 1786 | Apache-2.0 | Python | «Claude Code для финансового рынка» | взять идею |
| 51bitquant/ai-hedge-fund-crypto | https://github.com/51bitquant/ai-hedge-fund-crypto | 624 | MIT | Python | ИИ-хедж-фонд **для крипты** на LLM-агентах | взять идею |
| ZhuLinsen/alphasift | https://github.com/ZhuLinsen/alphasift | 368 | Apache-2.0 | Python | AI-native скрининг-движок: полный рынок → LLM-ранжирование → аудируемый скоринг | взять идею |
| Tomortec/CryptoTradingAgents | https://github.com/Tomortec/CryptoTradingAgents | 280 | Apache-2.0 | Python | Мультиагентный **крипто**-фреймворк | взять идею |
| huygiatrng/AlpacaTradingAgent | https://github.com/huygiatrng/AlpacaTradingAgent | 274 | Apache-2.0 | Python | Мультиагентный фреймворк (Alpaca) | взять идею |
| wshobson/mcp-trader | https://github.com/wshobson/mcp-trader | 273 | none | — | MCP-сервер для трейдеров (интеграция с LLM-клиентами) | взять идею |
| Superior-Trade/trading-terminal | https://github.com/Superior-Trade/trading-terminal | 155 | Apache-2.0 | TS | Self-hosted ИИ-терминал (Hyperliquid): считывает график → валидированная стратегия Freqtrade/Nautilus | взять идею |
| raskolnikoff/trader-ai | https://github.com/raskolnikoff/trader-ai | 3 | none | Python | Local-first ИИ-ассистент: TradingView + Claude + **RAG-память**, без облака | взять идею |
| cyber-turtle/AI-based-quant | https://github.com/cyber-turtle/AI-based-quant | 2 | MIT | Python | RAG-торговый ассистент (MT5) | взять идею |
| wickra-lib/wickra-copilot | https://github.com/wickra-lib/wickra-copilot | 3 | Apache-2.0 | Rust | LLM, «заземлённый» на стакан, ликвидации и фандинг | взять идею |
| RichChang963/Trading-Assistant | https://github.com/RichChang963/Trading-Assistant | 6 | NOASSERTION | Python | LLM + RAG для торгового анализа | взять идею |
| Limjunyi1/Finance-Stocks-RAG-Agent | https://github.com/Limjunyi1/Finance-Stocks-RAG-Agent | 2 | none | Python | Q&A-ассистент по торговле на RAG | взять идею |

**Вывод по разделу.** Слой LLM/RAG в нашем проекте — **самый копируемый**: паттерн
«мультиагент + дебаты + риск-менеджер» отработан в `TradingAgents` (109k★), а для крипты
есть готовые форки (`CryptoTradingAgents`, `ai-hedge-fund-crypto`). Для **RAG по методике**
прямой референс — `trader-ai` (local-first + RAG-память, ровно наш сценарий с локальными
моделями). Наш проект правил «числа считает код, а модель объясняет» соответствует их
архитектуре: агенты не считают, а интерпретируют. Ничего не берём целиком (тяжёлые,
не про плотность стакана), но **схему промптов и разделения ролей — берём**.

---

## 4. Библиотеки: ccxt-надстройки, сбор/хранение котировок, бэктест, детекторы уровней

### 4.1 Данные с бирж и сбор котировок

| Проект | Ссылка | ★ | Лицензия | Язык | Что даёт | Вердикт |
|---|---|---:|---|---|---|---|
| ccxt/ccxt | https://github.com/ccxt/ccxt | 44207 | MIT | Python/JS/TS | **Единый API к 100+ биржам** — фундамент нашего `src/data/` | **брать целиком** |
| bmoscon/cryptofeed | https://github.com/bmoscon/cryptofeed | 2913 | NOASSERTION | Python | **WebSocket-фиды** с бирж (L2/L3 стакан, трейды), асинхронно | **брать целиком** |
| bmoscon/cryptostore | https://github.com/bmoscon/cryptostore | 416 | NOASSERTION | Python | Масштабируемое **хранилище** крипто-данных (в паре с cryptofeed) | брать целиком |
| tardis-dev/tardis-machine | https://github.com/tardis-dev/tardis-machine | 313 | MPL-2.0 | TypeScript | Локальный сервер: **tick-level история + real-time**, встроенный кеш | взять |
| planet-winter/ccxt-ohlcv-fetcher | https://github.com/planet-winter/ccxt-ohlcv-fetcher | 24 | MIT | Python | Качает OHLCV через ccxt → sqlite | взять идею |
| Drakkar-Software/OctoBot-Trading | https://github.com/Drakkar-Software/OctoBot-Trading | 136 | LGPL-3.0 | Python | Библиотека-обёртка над CCXT для подключения к биржам | взять идею |
| qchef/cryptoDataUtility | https://github.com/qchef/cryptoDataUtility | 0 | MIT | Python | Бэкфилл публичных исторических данных Binance в **ClickHouse** | взять идею |
| bots2025/QuestDB | https://github.com/bots2025/QuestDB | 0 | none | Python | Хранение исторических + live данных в **QuestDB** | взять идею |
| skypad123/historical-price-cron-archiver | https://github.com/skypad123/historical-price-cron-archiver | 1 | none | Python | Архиватор OHLCV/ticker/стакана поминутно (CCXT + Celery) | взять идею |

### 4.2 Бэктест-фреймворки

| Проект | Ссылка | ★ | Лицензия | Язык | Что даёт | Вердикт |
|---|---|---:|---|---|---|---|
| freqtrade/freqtrade | https://github.com/freqtrade/freqtrade | 54909 | GPL-3.0 | Python | **Бот + бэктест + скринер**, экосистема стратегий | взять идею |
| nautechsystems/nautilus_trader | https://github.com/nautechsystems/nautilus_trader | 29493 | LGPL-3.0 | Rust | Production event-driven торговый движок | взять идею |
| polakowo/vectorbt | https://github.com/polakowo/vectorbt | 9210 | NOASSERTION | Python | **Векторный бэктест** — тысячи идей за секунды | брать целиком |
| jesse-ai/jesse | https://github.com/jesse-ai/jesse | 8595 | MIT | Python | Крипто-бот + бэктест | взять целиком |
| edtechre/pybroker | https://github.com/edtechre/pybroker | 3546 | NOASSERTION | Python | Алготрейдинг + ML | взять |
| blankly-finance/blankly | https://github.com/blankly-finance/blankly | 2478 | LGPL-3.0 | Python | Бэктест/деплой в пару строк | взять идею |
| ivopetiz/algotrading | https://github.com/ivopetiz/algotrading | 1687 | MIT | Python | Фреймворк алготрейдинга для крипты | взять |
| c9s/bbgo | https://github.com/c9s/bbgo | 1669 | AGPL-3.0 | Go | Бот-фреймворк | не подходит (Go) |
| 51bitquant/howtrader | https://github.com/51bitquant/howtrader | 964 | MIT | Python | Крипто-квант фреймворк (бэктест + исполнение) | взять |
| gbeced/basana | https://github.com/gbeced/basana | 869 | NOASSERTION | Python | Async event-driven фреймворк для крипты | взять идею |

### 4.3 Детекторы уровней, паттернов, индикаторы

| Проект | Ссылка | ★ | Лицензия | Язык | Что даёт | Вердикт |
|---|---|---:|---|---|---|---|
| mpquant/MyTT | https://github.com/mpquant/MyTT | 2870 | none | Python | **Все индикаторы в одном файле**, без TA-Lib | **брать целиком** |
| nardew/talipp | https://github.com/nardew/talipp | 539 | MIT | Python | **Инкрементальные** индикаторы (для стрима — идеально) | **брать целиком** |
| xgboosted/pandas-ta-classic | https://github.com/xgboosted/pandas-ta-classic | 447 | MIT | Python | 250+ индикаторов, pandas-расширение | брать целиком |
| gregyjames/ZenithTA | https://github.com/gregyjames/ZenithTA | 220 | MIT | Rust | Высокопроизводительные индикаторы (Rust + numpy) | взять идею |
| mr-easy/streaming_indicators | https://github.com/mr-easy/streaming_indicators | 153 | MIT | Python | Индикаторы на **потоковых** данных | брать целиком |
| ednunezg/pytrendline | https://github.com/ednunezg/pytrendline | 145 | MIT | Python | **Детекция трендлайнов поддержки/сопротивления**, с тюнингом | **брать целиком** |
| Prasad1612/smart-money-concept | https://github.com/Prasad1612/smart-money-concept | 47 | none | Python | **SMC:** структура рынка, order blocks, FVG, BOS/CHoCH | **брать целиком** |
| Prasad1612/SMC-Screener | https://github.com/Prasad1612/SMC-Screener | 4 | none | Python | Скринер на SMC-концепциях | взять идею |
| l33tquant/candlestick | https://github.com/l33tquant/candlestick | 11 | MIT | Rust | Распознавание свечных паттернов | взять идею |
| TulipCharts/tulipy | https://github.com/TulipCharts/tulipy | 377 | LGPL-3.0 | Python | Биндинги Tulip (не поддерживается) | не подходит |

### 4.4 Детекция манипуляций (pump&dump, wash trading)

| Проект | Ссылка | ★ | Лицензия | Язык | Что даёт | Вердикт |
|---|---|---:|---|---|---|---|
| SystemsLab-Sapienza/pump-and-dump-dataset | https://github.com/SystemsLab-Sapienza/pump-and-dump-dataset | 115 | MIT | Python | **Датасет** и код real-time детекции P&D | взять (данные) |
| Bayi-Hu/Pump-and-Dump-Detection-on-Cryptocurrency | https://github.com/Bayi-Hu/Pump-and-Dump-Detection-on-Cryptocurrency | 63 | none | Python | Детекция P&D по последовательностям (SIGMOD'23) | взять идею |
| Derposoft/crypto_pump_and_dump_with_deep_learning | https://github.com/Derposoft/crypto_pump_and_dump_with_deep_learning | 56 | none | Python | DL-исследование детекции P&D на малых монетах | взять идею |
| nitishabharathi/Washtrade-Detection | https://github.com/nitishabharathi/Washtrade-Detection | 7 | MIT | Python | Детекция wash-trading (динамическое программирование) | взять идею |
| rehan-ahmad25/crypto-pump-detection-system | https://github.com/rehan-ahmad25/crypto-pump-detection-system | 2 | MIT | HTML | Real-time аномалии: ML + статистика + граф-майнинг | взять идею |

---

## 5. Агрегаторы/клиенты бирж (аналоги Tiger.com Broker)

| Проект | Ссылка | ★ | Лицензия | Язык | Что даёт | Вердикт |
|---|---|---:|---|---|---|---|
| ccxt/ccxt | https://github.com/ccxt/ccxt | 44207 | MIT | Python/JS | Уже единый API к 100+ биржам — де-факто «агрегатор» | брать целиком |
| JKorf/Binance.Net | https://github.com/JKorf/Binance.Net | 1173 | MIT | C# | Полный клиент Binance REST + WS (spot/futures) | не подходит (C#) |
| Adamant-im/currencyinfo | https://github.com/Adamant-im/currencyinfo | 284 | GPL-3.0 | TypeScript | Self-hosted агрегатор курсов (крипта+фиат) за одним REST API | взять идею |
| createMonster/lotusx | https://github.com/createMonster/lotusx | 15 | MIT | Rust | Мультибиржевой коннектор-шлюз (Rust) | взять идею |
| 0xArchiveIO/sdk-python | https://github.com/0xArchiveIO/sdk-python | 8 | MIT | Python | Python-клиент **Hyperliquid** (perps/spot, гранулярные данные) | взять |
| goodylili/cex-aggregator | https://github.com/goodylili/cex-aggregator | 7 | MIT | Rust | Агрегатор цен по CEX (Rust) | взять идею |
| tesseralytics/python-client | https://github.com/tesseralytics/python-client | 2 | GPL-3.0 | Python | Клиент Hyperliquid market data API | взять идею |
| stephancill/universal-crypto-api | https://github.com/stephancill/universal-crypto-api | 2 | MIT | Python | Единый эндпоинт к популярным биржам | взять идею |
| goCyberTrade/broker_api_mcp | https://github.com/goCyberTrade/broker_api_mcp | 1 | none | Java | MCP-обёртка над брокерскими API (аккаунты, сделки) | взять идею |
| clawzhao/tiger_trade_bot | https://github.com/clawzhao/tiger_trade_bot | 1 | none | Python | Бот через **Tiger Open Platform API** (акции, не Tiger.com-крипта) | взять идею |
| tigusigalpa/coingecko-go | https://github.com/tigusigalpa/coingecko-go | 19 | MIT | Go | Клиент CoinGecko v3 | не подходит (Go) |

**Важно.** «Tiger.com Broker» (агрегатор над Binance/Bybit/OKX/Hyperliquid/Bitget/Gate с
кешбэком) — **проприетарный, открытого аналога нет**. `clawzhao/tiger_trade_bot` — про
другого «Tiger» (Tiger Brokers, акции). Модель кешбэка/агрегации в опенсорсе не
воспроизведена. Наш вывод: данные для скринера берём напрямую через `ccxt`/`cryptofeed`
с бирж, доступных напрямую (451/403 в этом проекте даёт не РФ, а прокси из США —
см. `docs/00-состояние.md` §4.0); Tiger.com оставляем как слой **исполнения и баланса**
(ему нужен приватный ключ), скринер на нём не строим — это совпадает с README проекта.

---

## 6. Готовые кубики для сборки (итоговая карта)

| Кубик нашей системы | Что берём | Откуда |
|---|---|---|
| Сбор публичных данных (свечи, стакан, трейды) | готовая библиотека | `ccxt/ccxt` + `bmoscon/cryptofeed` |
| Хранение (parquet/БД) | концепт + код | `cryptostore`, `cryptoDataUtility` (ClickHouse), `QuestDB`-примеры |
| Индикаторы (инкрементально, на стриме) | готовая библиотека | `talipp`, `streaming_indicators`, `MyTT` |
| Уровни поддержки/сопротивления | готовая библиотека | `ednunezg/pytrendline` |
| Структура рынка / BOS / FVG / liquidity sweep | готовая библиотека | `Prasad1612/smart-money-concept` |
| Детекция формаций (breakout/retest/spring) | **пишем сами** (готового нет) | идеи скоринга у `NadirAliOfficial/trading-scanner` |
| Плотность стакана: footprint, delta, volume profile | концепт + Python-код | `mahmoud20138/OrderFlow-Analysis-Pro` |
| Плотность стакана: heatmap real-time | архитектура | `flowsurface-rs/flowsurface`, `DegenSugarBoo/OpenBook` |
| Spoofing / iceberg / walls | **пишем сами** (только прототипы) | метрики у `alphacore`, `market-surveillance-toolkit`, VPIN/OBI |
| Алерты (Telegram) | идея + код | `NadirAliOfficial/trading-scanner`, `luckyx7777/pump-dump-screener` |
| RAG по методике | архитектура | `raskolnikoff/trader-ai` (local-first + RAG) |
| LLM-предложение входа (роли агентов, промпты) | архитектура | `TauricResearch/TradingAgents`, `Tomortec/CryptoTradingAgents` |
| Бэктест отработки стратегии | готовая библиотека | `polakowo/vectorbt` (+ `jesse`/`howtrader` для исполнения) |

## 7. Проекты, которые ближе всего к «плотность стакана + формации + алерты»

Ни один не закрывает всё три сразу — но эти четыре наиболее близки к нашей задаче,
с них и надо начинать разбор кода:

1. **AIUngated/crypto-liquidity-terminal** (MIT, TS, 5★) — скринер + heatmap плотности
   стакана Binance, мультибиржа. Ближе всех по **замыслу объёма**, но молодой.
2. **mahmoud20138/OrderFlow-Analysis-Pro** (MIT, Python, 59★) — footprint, delta,
   volume profile, детекция паттернов. Лучший **готовый Python-кубик по стакану**.
3. **bigmacman1129/crypto-ai-trading-bot** (JS, 194★) — анализ стакана, кластеры стопов,
   liquidity sweep + бот. Лицензии нет — берём только идеи.
4. **NadirAliOfficial/trading-scanner** (Python, 14★) — BOS/FVG/liquidity sweep +
   скоринг сетапа + Telegram-алерты (на MT5, но логику переносим на крипту).

**Резюме для решения:** не изобретаем заново — ставим `ccxt`+`cryptofeed` как источник,
`vectorbt` как бэктест, `talipp`/`pytrendline`/`smart-money-concept` как индикаторы и
структуру; формации, метрики плотности и spoof/iceberg-детекторы пишем сами, опираясь на
идеи из 0–60★ прототипов; слой LLM/RAG копируем архитектурно с `TradingAgents` и
`trader-ai`.
