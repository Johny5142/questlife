# QuestLife — Telegram Mini App

Геймифицированный RPG-менеджер привычек и задач в виде Telegram Mini App.

## Возможности

### Квесты
- **Ежедневные квесты** — задачи на конкретную дату
- **Глобальные квесты 🌐** — без привязки к дате, видны каждый день
- **Квесты с дедлайном ⏳** — с точным сроком выполнения
- **Группы задач** — организация по категориям (Здоровье, Работа, Учёба)
- **Удаление** — выполненные и невыполненные задачи
- Создание через модалку: тип квеста, группа, напоминание

### Геймификация
- **XP и золото** — награды за выполнение квестов
- **Дневные лимиты**: 200 XP и 150 золота в день
- **Прогрессивные уровни**: 100 XP → 300 XP → 500 XP (каждый уровень +200)
- **Магазин** — пользовательские награды с картинками 1:1

### ИИ-оценка задач
- Кнопка «✨ Оценить ИИ» — DeepSeek анализирует сложность каждой задачи
- Адекватные награды: простая = 10-15 XP, сложная = 50-120 XP
- Учитывает дневные лимиты при распределении
- Оценка необязательна: без неё задача выполняется с 0 наградой

### Уведомления в Telegram
- **Личные напоминания ⏰** — на конкретное время для любой задачи
- **Дайджест каждые 12 часов** — квесты на ближайшие 24 часа
- **Дайджест каждые 24 часа** — невыполненные глобальные и квесты с дедлайнами

### Рейтинг
- Топ-50 игроков по общему XP
- Приватность: кнопка «Скрыть меня» — пользователь не попадает в рейтинг

## Стек

- **Backend**: Python, FastAPI, SQLAlchemy, SQLite
- **Frontend**: HTML5, Tailwind CSS, Vanilla JS
- **AI**: DeepSeek (OpenAI-совместимый API)
- **Bot**: Telegram Bot API
- **Deploy**: Railway

## Переменные окружения

| Переменная | Описание | Пример |
|---|---|---|
| `BOT_TOKEN` | Токен бота от @BotFather | `123456:ABC-...` |
| `DEEPSEEK_API_KEY` | API-ключ DeepSeek | `sk-...` |
| `DEEPSEEK_BASE_URL` | Base URL API | `https://xyvero.space/v1` |
| `DEEPSEEK_MODEL` | Модель | `deepseek-v4.1-flash` |
| `DATABASE_PATH` | Путь к SQLite | `/data/questlife.db` |

## Локальный запуск

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Файл `.env`:
```
BOT_TOKEN=токен_бота
DEEPSEEK_API_KEY=твой_ключ
```

## Деплой на Railway

1. Подключи GitHub-репозиторий
2. Сгенерируй домен (Networking → Generate Domain)
3. Добавь переменные из таблицы выше
4. Создай Volume с mount path `/data` — для сохранения базы
5. Railway задеплоит автоматически

## Структура

- `main.py` — FastAPI backend (API, БД, ИИ, уведомления)
- `bot.py` — регистрация Mini App в Telegram
- `static/index.html` — frontend Mini App
- `static/uploads/` — картинки магазина (gitignored)
- `questlife.db` — база данных (gitignored)

## Архитектура

```
Telegram → @quest_life2_bot → кнопка QuestLife
                                    ↓
         https://questlife-production.up.railway.app
                                    ↓
         Railway (FastAPI) ←→ DeepSeek API (оценка задач)
                ↓
         SQLite (/data volume)
                ↓
         Bot API (напоминания, дайджесты)
```
