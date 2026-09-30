# QuestLife — Telegram Mini App (геймифицированный трекер привычек)

import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Date, text, create_engine, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Игровые константы
BASE_XP_PER_LEVEL = 100
LEVEL_XP_STEP = 200
DAILY_XP_LIMIT = 200
DAILY_GOLD_LIMIT = 150

# DeepSeek AI
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", os.getenv("GIGACHAT_API_KEY", ""))
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://xyvero.space/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4.1-flash")
DEEPSEEK_API_URL = f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def resolve_db_path() -> Path:
    candidates = []
    env_path = os.getenv("DATABASE_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("/data/questlife.db"))
    candidates.append(BASE_DIR / "data" / "questlife.db")
    candidates.append(BASE_DIR / "questlife.db")
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            probe = p.parent / ".write_test"
            probe.touch()
            probe.unlink()
            return p
        except Exception:
            continue
    return BASE_DIR / "questlife.db"


DB_PATH = resolve_db_path()
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

UPLOAD_DIR = DB_PATH.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# Модели
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    level = Column(Integer, default=1, nullable=False)
    xp = Column(Integer, default=0, nullable=False)
    gold = Column(Integer, default=0, nullable=False)
    total_xp = Column(Integer, default=0, nullable=False)
    last_active_date = Column(Date, default=date.today, nullable=False)
    daily_xp_gained = Column(Integer, default=0, nullable=False)
    daily_gold_gained = Column(Integer, default=0, nullable=False)
    show_in_leaderboard = Column(Boolean, default=True, nullable=False)

    habits = relationship("Habit", back_populates="user", cascade="all, delete-orphan")
    items = relationship("Item", back_populates="user", cascade="all, delete-orphan")


class Habit(Base):
    __tablename__ = "habits"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    group_name = Column(String, nullable=True, default=None)
    scheduled_date = Column(Date, default=date.today, nullable=False)
    xp_reward = Column(Integer, default=0, nullable=False)
    gold_reward = Column(Integer, default=0, nullable=False)
    ai_evaluated = Column(Boolean, default=False, nullable=False)
    reminder_time = Column(String, nullable=True)  # HH:MM
    is_global = Column(Boolean, default=False, nullable=False)  # глобальный квест
    deadline = Column(DateTime, nullable=True)  # дедлайн для глобальных
    is_completed = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="habits")


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    price = Column(Integer, nullable=False, default=10)
    image_path = Column(String, nullable=True)
    is_purchased = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="items")


# Миграции
def run_migrations():
    with engine.connect() as conn:
        for col, typedef in [
            ("total_xp", "INTEGER DEFAULT 0"),
            ("daily_xp_gained", "INTEGER DEFAULT 0"),
            ("daily_gold_gained", "INTEGER DEFAULT 0"),
            ("show_in_leaderboard", "BOOLEAN DEFAULT 1"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typedef}"))
                print(f"[migrate] users.{col} added")
            except Exception:
                pass

        for col, typedef in [
            ("group_name", "TEXT DEFAULT NULL"),
            ("scheduled_date", "DATE"),
            ("xp_reward", "INTEGER DEFAULT 0"),
            ("gold_reward", "INTEGER DEFAULT 0"),
            ("ai_evaluated", "BOOLEAN DEFAULT 0"),
            ("reminder_time", "TEXT DEFAULT NULL"),
            ("is_global", "BOOLEAN DEFAULT 0"),
            ("deadline", "DATETIME DEFAULT NULL"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE habits ADD COLUMN {col} {typedef}"))
                print(f"[migrate] habits.{col} added")
            except Exception:
                pass

        try:
            conn.execute(text("UPDATE habits SET scheduled_date = :d WHERE scheduled_date IS NULL"), {"d": date.today().isoformat()})
        except Exception:
            pass
        conn.commit()


Base.metadata.create_all(engine)
run_migrations()


# Хелперы
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def xp_needed_for_level(level: int) -> int:
    return BASE_XP_PER_LEVEL + (level - 1) * LEVEL_XP_STEP


def user_response(user: User) -> dict:
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "level": user.level,
        "xp": user.xp,
        "xp_needed": xp_needed_for_level(user.level),
        "gold": user.gold,
        "total_xp": (user.total_xp or 0) + user.xp,
        "daily_xp_remaining": max(0, DAILY_XP_LIMIT - user.daily_xp_gained),
        "daily_gold_remaining": max(0, DAILY_GOLD_LIMIT - user.daily_gold_gained),
        "show_in_leaderboard": user.show_in_leaderboard,
    }


def habit_response(habit: Habit) -> dict:
    return {
        "id": habit.id,
        "title": habit.title,
        "group_name": habit.group_name,
        "scheduled_date": habit.scheduled_date.isoformat() if habit.scheduled_date else None,
        "xp_reward": habit.xp_reward,
        "gold_reward": habit.gold_reward,
        "ai_evaluated": habit.ai_evaluated,
        "reminder_time": habit.reminder_time,
        "is_global": habit.is_global,
        "deadline": habit.deadline.isoformat() if habit.deadline else None,
        "is_completed": habit.is_completed,
        "updated_at": habit.updated_at.isoformat() if habit.updated_at else None,
    }


def item_response(item: Item) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "price": item.price,
        "image_path": item.image_path,
        "is_purchased": item.is_purchased,
    }


def get_user_by_tg_id(db: Session, tg_id: int) -> Optional[User]:
    return db.query(User).filter(User.telegram_id == tg_id).first()


def maybe_reset_daily(db: Session, user: User) -> None:
    today = utc_now().date()
    if user.last_active_date != today:
        user.last_active_date = today
        user.daily_xp_gained = 0
        user.daily_gold_gained = 0
        for habit in user.habits:
            if habit.is_completed and habit.scheduled_date == today:
                habit.is_completed = False
        db.commit()


# DeepSeek
def evaluate_tasks_with_ai(tasks: list) -> dict:
    if not DEEPSEEK_API_KEY:
        return {}
    prompt = f"""Ты — геймдизайнер RPG-менеджера привычек. Оцени важность и сложность каждой задачи.

ДНЕВНЫЕ ЛИМИТЫ: максимум {DAILY_XP_LIMIT} XP и {DAILY_GOLD_LIMIT} золота НА ВСЕ задачи суммарно.

Задачи:
{chr(10).join(f"{i+1}. {t}" for i, t in enumerate(tasks))}

Оцени каждую задачу и выдай награду:
- Простая (5 мин): xp=10-15, gold=5-10
- Средняя (30 мин): xp=20-40, gold=15-30
- Сложная (1+ час): xp=50-80, gold=40-60
- Очень сложная: xp=80-120, gold=60-90

Сумма всех наград НЕ должна превышать лимиты. Оценивай по реальной сложности.

Ответ СТРОГО в JSON: {{"tasks": [{{"title": "...", "xp": N, "gold": N}}]}}"""

    import time as _time
    last_error = ""
    for attempt in range(3):
        try:
            res = requests.post(
                DEEPSEEK_API_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={"model": DEEPSEEK_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 1000},
                timeout=60,
            )
            print(f"[deepseek] attempt={attempt+1} status={res.status_code}")
            if res.status_code == 200:
                content = res.json()["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                data = json.loads(content)
                result = {}
                for t in data.get("tasks", []):
                    result[t["title"]] = {"xp": min(int(t.get("xp", 15)), 100), "gold": min(int(t.get("gold", 10)), 80)}
                return result
            else:
                last_error = f"HTTP {res.status_code}"
                if res.status_code in (502, 503, 504) and attempt < 2:
                    _time.sleep(3)
                    continue
        except Exception as e:
            last_error = str(e)
            print(f"[deepseek] attempt={attempt+1} error: {e}")
            if attempt < 2:
                _time.sleep(3)
    print(f"[deepseek] all attempts failed: {last_error}")
    return {}


# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def resolve_db_path() -> Path:
    candidates = []
    env_path = os.getenv("DATABASE_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("/data/questlife.db"))
    candidates.append(BASE_DIR / "data" / "questlife.db")
    candidates.append(BASE_DIR / "questlife.db")
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            probe = p.parent / ".write_test"
            probe.touch()
            probe.unlink()
            return p
        except Exception:
            continue
    return BASE_DIR / "questlife.db"


DB_PATH = resolve_db_path()
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

UPLOAD_DIR = DB_PATH.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# Модели
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    level = Column(Integer, default=1, nullable=False)
    xp = Column(Integer, default=0, nullable=False)
    gold = Column(Integer, default=0, nullable=False)
    total_xp = Column(Integer, default=0, nullable=False)
    last_active_date = Column(Date, default=date.today, nullable=False)
    daily_xp_gained = Column(Integer, default=0, nullable=False)
    daily_gold_gained = Column(Integer, default=0, nullable=False)
    show_in_leaderboard = Column(Boolean, default=True, nullable=False)

    habits = relationship("Habit", back_populates="user", cascade="all, delete-orphan")
    items = relationship("Item", back_populates="user", cascade="all, delete-orphan")


class Habit(Base):
    __tablename__ = "habits"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    group_name = Column(String, nullable=True, default=None)
    scheduled_date = Column(Date, default=date.today, nullable=False)
    xp_reward = Column(Integer, default=0, nullable=False)
    gold_reward = Column(Integer, default=0, nullable=False)
    ai_evaluated = Column(Boolean, default=False, nullable=False)
    reminder_time = Column(String, nullable=True)  # HH:MM
    is_global = Column(Boolean, default=False, nullable=False)  # глобальный квест
    deadline = Column(DateTime, nullable=True)  # дедлайн для глобальных
    is_completed = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="habits")


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    price = Column(Integer, nullable=False, default=10)
    image_path = Column(String, nullable=True)
    is_purchased = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="items")


# Миграции
def run_migrations():
    with engine.connect() as conn:
        for col, typedef in [
            ("total_xp", "INTEGER DEFAULT 0"),
            ("daily_xp_gained", "INTEGER DEFAULT 0"),
            ("daily_gold_gained", "INTEGER DEFAULT 0"),
            ("show_in_leaderboard", "BOOLEAN DEFAULT 1"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typedef}"))
                print(f"[migrate] users.{col} added")
            except Exception:
                pass

        for col, typedef in [
            ("group_name", "TEXT DEFAULT NULL"),
            ("scheduled_date", "DATE"),
            ("xp_reward", "INTEGER DEFAULT 0"),
            ("gold_reward", "INTEGER DEFAULT 0"),
            ("ai_evaluated", "BOOLEAN DEFAULT 0"),
            ("reminder_time", "TEXT DEFAULT NULL"),
            ("is_global", "BOOLEAN DEFAULT 0"),
            ("deadline", "DATETIME DEFAULT NULL"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE habits ADD COLUMN {col} {typedef}"))
                print(f"[migrate] habits.{col} added")
            except Exception:
                pass

        try:
            conn.execute(text("UPDATE habits SET scheduled_date = :d WHERE scheduled_date IS NULL"), {"d": date.today().isoformat()})
        except Exception:
            pass
        conn.commit()


Base.metadata.create_all(engine)
run_migrations()


# Хелперы
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def xp_needed_for_level(level: int) -> int:
    return BASE_XP_PER_LEVEL + (level - 1) * LEVEL_XP_STEP


def user_response(user: User) -> dict:
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "level": user.level,
        "xp": user.xp,
        "xp_needed": xp_needed_for_level(user.level),
        "gold": user.gold,
        "total_xp": (user.total_xp or 0) + user.xp,
        "daily_xp_remaining": max(0, DAILY_XP_LIMIT - user.daily_xp_gained),
        "daily_gold_remaining": max(0, DAILY_GOLD_LIMIT - user.daily_gold_gained),
        "show_in_leaderboard": user.show_in_leaderboard,
    }


def habit_response(habit: Habit) -> dict:
    return {
        "id": habit.id,
        "title": habit.title,
        "group_name": habit.group_name,
        "scheduled_date": habit.scheduled_date.isoformat() if habit.scheduled_date else None,
        "xp_reward": habit.xp_reward,
        "gold_reward": habit.gold_reward,
        "ai_evaluated": habit.ai_evaluated,
        "reminder_time": habit.reminder_time,
        "is_global": habit.is_global,
        "deadline": habit.deadline.isoformat() if habit.deadline else None,
        "is_completed": habit.is_completed,
        "updated_at": habit.updated_at.isoformat() if habit.updated_at else None,
    }


def item_response(item: Item) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "price": item.price,
        "image_path": item.image_path,
        "is_purchased": item.is_purchased,
    }


def get_user_by_tg_id(db: Session, tg_id: int) -> Optional[User]:
    return db.query(User).filter(User.telegram_id == tg_id).first()


def maybe_reset_daily(db: Session, user: User) -> None:
    today = utc_now().date()
    if user.last_active_date != today:
        user.last_active_date = today
        user.daily_xp_gained = 0
        user.daily_gold_gained = 0
        for habit in user.habits:
            if habit.is_completed and habit.scheduled_date == today:
                habit.is_completed = False
        db.commit()


# DeepSeek
# Telegram
async def send_telegram_message(chat_id: int, text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        res = requests.post(
            TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return res.status_code == 200
    except Exception as e:
        print(f"[notify] error: {e}")
        return False


# Схемы
class HabitCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    group_name: Optional[str] = Field(default=None, max_length=60)
    scheduled_date: Optional[str] = None
    reminder_time: Optional[str] = None  # HH:MM
    is_global: Optional[bool] = False
    deadline: Optional[str] = None  # ISO datetime


class HabitUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    group_name: Optional[str] = Field(default=None, max_length=60)
    scheduled_date: Optional[str] = None
    reminder_time: Optional[str] = None


# Приложение
app = FastAPI(title="QuestLife API", version="2.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def start_reminder_loop():
    import asyncio

    async def reminder_loop():
        while True:
            try:
                db = SessionLocal()
                now = datetime.now(timezone.utc)
                now_hm = now.strftime("%H:%M")
                today = now.date()
                due = db.query(Habit).join(User).filter(
                    Habit.reminder_time == now_hm,
                    Habit.is_completed == False,  # noqa: E712
                    Habit.scheduled_date == today,
                ).all()
                for habit in due:
                    if await send_telegram_message(habit.user.telegram_id, f"⏰ <b>Напоминание</b>\n\n{habit.title}\n\nНе забудь выполнить!"):
                        habit.reminder_time = None
                        db.commit()
            except Exception as e:
                print(f"[reminder] error: {e}")
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            await asyncio.sleep(60)

    asyncio.create_task(reminder_loop())


# Профиль
@app.get("/api/user/{tg_id}", tags=["users"])
def get_or_create_user(tg_id: int, username: Optional[str] = None, db: Session = Depends(get_db)):
    user = get_user_by_tg_id(db, tg_id)
    if user is None:
        user = User(telegram_id=tg_id, username=username)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif username and user.username != username:
        user.username = username
        db.commit()
    maybe_reset_daily(db, user)
    return user_response(user)


@app.post("/api/user/{tg_id}/toggle-leaderboard", tags=["users"])
def toggle_leaderboard(tg_id: int, db: Session = Depends(get_db)):
    user = get_user_by_tg_id(db, tg_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.show_in_leaderboard = not user.show_in_leaderboard
    db.commit()
    db.refresh(user)
    return user_response(user)


# Квесты
@app.get("/api/habits/{tg_id}", tags=["habits"])
def get_habits(tg_id: int, date_filter: Optional[str] = None, db: Session = Depends(get_db)):
    user = get_user_by_tg_id(db, tg_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    maybe_reset_daily(db, user)

    query = db.query(Habit).filter(Habit.user_id == user.id)
    if date_filter:
        try:
            target = date.fromisoformat(date_filter)
            query = query.filter((Habit.scheduled_date == target) | (Habit.is_global == True))  # noqa: E712
        except ValueError:
            raise HTTPException(status_code=422, detail="Неверный формат даты")
    else:
        query = query.filter((Habit.scheduled_date >= utc_now().date()) | (Habit.is_global == True))  # noqa: E712

    habits = query.order_by(Habit.scheduled_date, Habit.group_name, Habit.id).all()
    return [habit_response(h) for h in habits]


@app.post("/api/habits/{tg_id}", tags=["habits"])
def create_habit(tg_id: int, habit: HabitCreate, db: Session = Depends(get_db)):
    user = get_user_by_tg_id(db, tg_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    target_date = utc_now().date()
    if habit.scheduled_date:
        try:
            target_date = date.fromisoformat(habit.scheduled_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Неверный формат даты")

    reminder = None
    if habit.reminder_time:
        try:
            datetime.strptime(habit.reminder_time, "%H:%M")
            reminder = habit.reminder_time
        except ValueError:
            raise HTTPException(status_code=422, detail="Время в формате HH:MM")

    deadline_dt = None
    if habit.deadline:
        try:
            deadline_dt = datetime.fromisoformat(habit.deadline)
        except ValueError:
            raise HTTPException(status_code=422, detail="Дедлайн в формате ISO")

    is_global = bool(habit.is_global) or deadline_dt is not None
    # scheduled_date оставляем (NOT NULL в старой БД), но is_global выводит квест из дневного фильтра

    new_habit = Habit(
        user_id=user.id,
        title=habit.title.strip(),
        group_name=habit.group_name.strip() if habit.group_name else None,
        scheduled_date=target_date,
        reminder_time=reminder,
        is_global=is_global,
        deadline=deadline_dt,
    )
    db.add(new_habit)
    db.commit()
    db.refresh(new_habit)
    return habit_response(new_habit)


@app.put("/api/habits/{habit_id}", tags=["habits"])
def update_habit(habit_id: int, data: HabitUpdate, db: Session = Depends(get_db)):
    habit = db.query(Habit).filter(Habit.id == habit_id).first()
    if habit is None:
        raise HTTPException(status_code=404, detail="Квест не найден")
    if data.title is not None:
        habit.title = data.title.strip()
    if data.group_name is not None:
        habit.group_name = data.group_name.strip() if data.group_name else None
    if data.scheduled_date:
        habit.scheduled_date = date.fromisoformat(data.scheduled_date)
    if data.reminder_time is not None:
        habit.reminder_time = data.reminder_time or None
    db.commit()
    db.refresh(habit)
    return habit_response(habit)


@app.delete("/api/habits/{habit_id}", tags=["habits"])
def delete_habit(habit_id: int, db: Session = Depends(get_db)):
    habit = db.query(Habit).filter(Habit.id == habit_id).first()
    if habit is None:
        raise HTTPException(status_code=404, detail="Квест не найден")
    db.delete(habit)
    db.commit()
    return {"ok": True}


@app.post("/api/habits/{habit_id}/complete", tags=["habits"])
def complete_habit(habit_id: int, db: Session = Depends(get_db)):
    habit = db.query(Habit).join(User).filter(Habit.id == habit_id).first()
    if habit is None:
        raise HTTPException(status_code=404, detail="Квест не найден")
    if habit.is_completed:
        raise HTTPException(status_code=400, detail="Квест уже выполнен")

    user = habit.user
    maybe_reset_daily(db, user)
    xp_left = max(0, DAILY_XP_LIMIT - user.daily_xp_gained)
    gold_left = max(0, DAILY_GOLD_LIMIT - user.daily_gold_gained)
    actual_xp = min(habit.xp_reward or 0, xp_left)
    actual_gold = min(habit.gold_reward or 0, gold_left)

    habit.is_completed = True
    habit.updated_at = utc_now()
    user.xp += actual_xp
    user.gold += actual_gold
    user.total_xp = (user.total_xp or 0) + actual_xp
    user.daily_xp_gained += actual_xp
    user.daily_gold_gained += actual_gold

    leveled_up = False
    while user.xp >= xp_needed_for_level(user.level):
        user.xp -= xp_needed_for_level(user.level)
        user.level += 1
        leveled_up = True

    db.commit()
    db.refresh(user)
    db.refresh(habit)
    return {"habit": habit_response(habit), "user": user_response(user), "actual_xp": actual_xp, "actual_gold": actual_gold, "leveled_up": leveled_up}


# ИИ
@app.post("/api/evaluate/{tg_id}", tags=["ai"])
def evaluate_tasks(tg_id: int, db: Session = Depends(get_db)):
    user = get_user_by_tg_id(db, tg_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=400, detail="DEEPSEEK_API_KEY не настроен")
    maybe_reset_daily(db, user)
    today = utc_now().date()
    pending = db.query(Habit).filter(
        Habit.user_id == user.id, Habit.scheduled_date == today, Habit.is_completed == False  # noqa: E712
    ).all()
    if not pending:
        raise HTTPException(status_code=400, detail="Нет невыполненных задач на сегодня")

    evaluations = evaluate_tasks_with_ai([h.title for h in pending])
    if not evaluations:
        raise HTTPException(status_code=502, detail="Сервис ИИ временно недоступен (503). Это проблема сервера xyvero.space — повторите через пару минут.")

    xp_budget = max(0, DAILY_XP_LIMIT - user.daily_xp_gained)
    gold_budget = max(0, DAILY_GOLD_LIMIT - user.daily_gold_gained)
    updated = 0
    for habit in pending:
        if habit.title in evaluations:
            ev = evaluations[habit.title]
            habit.xp_reward = min(ev["xp"], xp_budget)
            habit.gold_reward = min(ev["gold"], gold_budget)
            habit.ai_evaluated = True
            updated += 1
    db.commit()
    return {"updated": updated, "habits": [habit_response(h) for h in pending], "xp_budget": xp_budget, "gold_budget": gold_budget}


# Рейтинг
@app.get("/api/leaderboard", tags=["leaderboard"])
def get_leaderboard(db: Session = Depends(get_db)):
    users = (
        db.query(User)
        .filter(User.show_in_leaderboard != False)  # noqa: E712
        .order_by(User.total_xp.desc(), User.level.desc())
        .limit(50)
        .all()
    )
    return [
        {"rank": i + 1, "telegram_id": u.telegram_id, "username": u.username, "level": u.level, "total_xp": (u.total_xp or 0) + u.xp, "gold": u.gold}
        for i, u in enumerate(users)
    ]


# Магазин
@app.get("/api/items/{tg_id}", tags=["items"])
def get_items(tg_id: int, db: Session = Depends(get_db)):
    user = get_user_by_tg_id(db, tg_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    items = db.query(Item).filter(Item.user_id == user.id).order_by(Item.id).all()
    return [item_response(i) for i in items]


@app.post("/api/items/{tg_id}", tags=["items"])
async def create_item(
    tg_id: int,
    title: str = Form(...),
    price: int = Form(...),
    image: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    user = get_user_by_tg_id(db, tg_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if price < 1:
        raise HTTPException(status_code=422, detail="Цена должна быть не меньше 1")

    image_path = None
    if image and image.filename:
        content = await image.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Файл больше 10 МБ")
        if not (content.startswith(b"\x89PNG") or content.startswith(b"\xff\xd8\xff")):
            raise HTTPException(status_code=415, detail="Поддерживаются только PNG и JPG")

        # Автообрезка до 1:1 без потери качества
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(content))
            w, h = img.size
            if abs(w - h) > 1:
                # Обрезаем по центру до квадрата
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                img = img.crop((left, top, left + side, top + side))
                # Сохраняем в JPEG (кроме прозрачных PNG)
                buf = io.BytesIO()
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(buf, format="JPEG", quality=95)
                content = buf.getvalue()
                ext = ".jpg"
            else:
                ext = ".png" if content.startswith(b"\x89PNG") else ".jpg"
        except ImportError:
            ext = ".png" if content.startswith(b"\x89PNG") else ".jpg"

        filename = f"{secrets.token_hex(8)}{ext}"
        (UPLOAD_DIR / filename).write_bytes(content)
        image_path = f"/uploads/{filename}"

    new_item = Item(user_id=user.id, title=title.strip(), price=price, image_path=image_path)
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    return item_response(new_item)


@app.post("/api/items/{item_id}/buy", tags=["items"])
def buy_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(Item).join(User).filter(Item.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Предмет не найден")
    if item.is_purchased:
        raise HTTPException(status_code=400, detail="Предмет уже куплен")
    user = item.user
    if user.gold < item.price:
        raise HTTPException(status_code=400, detail="Недостаточно золота")
    user.gold -= item.price
    item.is_purchased = True
    db.commit()
    db.refresh(user)
    db.refresh(item)
    return {"item": item_response(item), "user": user_response(user)}



# ------------------------------------------------------------
# Webhook: ответ на /start с кнопкой открытия Mini App
# ------------------------------------------------------------

@app.post("/webhook")
async def telegram_webhook(update: dict):
    """Обрабатывает /start: присылает сообщение с inline-кнопкой «Открыть»."""
    try:
        message = update.get("message")
        if message and message.get("text", "").startswith("/start"):
            chat_id = message["chat"]["id"]
            requests.post(
                TELEGRAM_API_URL.replace("/sendMessage", "/sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": "⚔️ QuestLife\n\nГеймифицированный трекер привычек!\n\n• Зарабатывай XP и золото\n• Выполняй квесты\n• Побеждай в рейтинге\n\nНажми кнопку, чтобы начать:",
                    "reply_markup": {
                        "inline_keyboard": [[
                            {"text": "🚀 Открыть QuestLife", "web_app": {"url": "https://questlife-production.up.railway.app"}}
                        ]]
                    },
                },
                timeout=10,
            )
    except Exception as e:
        print(f"[webhook] error: {e}")
    return {"ok": True}

# Статика
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
