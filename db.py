import sqlite3
from datetime import datetime, timedelta

DB_PATH = "raid_schedule.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            creator_id INTEGER NOT NULL,
            creator_name TEXT NOT NULL,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            day_key TEXT NOT NULL,
            time_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(schedule_id, user_id, day_key),
            FOREIGN KEY(schedule_id) REFERENCES schedules(id)
        )
        """
    )

    conn.commit()
    conn.close()


def create_schedule_record(schedule):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO schedules (
            title,
            creator_id,
            creator_name,
            guild_id,
            channel_id,
            message_id,
            week_start,
            week_end,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            schedule["title"],
            schedule["creator_id"],
            schedule["creator_name"],
            schedule["guild_id"],
            schedule["channel_id"],
            schedule.get("message_id"),
            schedule["week_start"],
            schedule["week_end"],
        ),
    )

    schedule_id = cur.lastrowid

    conn.commit()
    conn.close()

    return schedule_id


def update_schedule_message_id(schedule_id, message_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE schedules
        SET message_id = ?
        WHERE id = ?
        """,
        (message_id, schedule_id),
    )

    conn.commit()
    conn.close()


def save_availability(schedule_id, user_id, user_name, day_key, time_value):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO availability (
            schedule_id,
            user_id,
            user_name,
            day_key,
            time_value,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(schedule_id, user_id, day_key)
        DO UPDATE SET
            user_name = excluded.user_name,
            time_value = excluded.time_value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            schedule_id,
            str(user_id),
            user_name,
            day_key,
            time_value,
        ),
    )

    conn.commit()
    conn.close()


def delete_availability_day(schedule_id, user_id, day_key):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM availability
        WHERE schedule_id = ?
          AND user_id = ?
          AND day_key = ?
        """,
        (
            schedule_id,
            str(user_id),
            day_key,
        ),
    )

    conn.commit()
    conn.close()


def delete_availability_user(schedule_id, user_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM availability
        WHERE schedule_id = ?
          AND user_id = ?
        """,
        (
            schedule_id,
            str(user_id),
        ),
    )

    conn.commit()
    conn.close()


def deactivate_schedule(schedule_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE schedules
        SET is_active = 0
        WHERE id = ?
        """,
        (schedule_id,),
    )

    conn.commit()
    conn.close()


def build_week_dates_from_start(week_start_text):
    week_start = datetime.strptime(week_start_text, "%Y-%m-%d")
    return [week_start + timedelta(days=i) for i in range(7)]


def load_active_schedules():
    conn = get_connection()
    cur = conn.cursor()

    schedule_rows = cur.execute(
        """
        SELECT *
        FROM schedules
        WHERE is_active = 1
        ORDER BY id ASC
        """
    ).fetchall()

    availability_rows = cur.execute(
        """
        SELECT *
        FROM availability
        ORDER BY schedule_id ASC, user_name ASC, day_key ASC
        """
    ).fetchall()

    conn.close()

    schedules = {}

    for row in schedule_rows:
        schedule_id = row["id"]

        week_dates = build_week_dates_from_start(row["week_start"])

        days = {}
        for date_obj in week_dates:
            day_key = date_obj.strftime("%Y-%m-%d")
            weekdays = ["월", "화", "수", "목", "금", "토", "일"]
            days[day_key] = f"{date_obj.strftime('%m/%d')} ({weekdays[date_obj.weekday()]})"

        schedules[schedule_id] = {
            "id": schedule_id,
            "title": row["title"],
            "creator_id": row["creator_id"],
            "creator_name": row["creator_name"],
            "guild_id": row["guild_id"],
            "channel_id": row["channel_id"],
            "message_id": row["message_id"],
            "week_start": row["week_start"],
            "week_end": row["week_end"],
            "week_dates": week_dates,
            "week_start_label": days[week_dates[0].strftime("%Y-%m-%d")],
            "week_end_label": days[week_dates[-1].strftime("%Y-%m-%d")],
            "days": days,
            "availability": {},
            "summary": {},
        }

    for row in availability_rows:
        schedule_id = row["schedule_id"]

        if schedule_id not in schedules:
            continue

        user_id = str(row["user_id"])

        if user_id not in schedules[schedule_id]["availability"]:
            schedules[schedule_id]["availability"][user_id] = {
                "name": row["user_name"],
                "selected": {},
            }

        schedules[schedule_id]["availability"][user_id]["name"] = row["user_name"]
        schedules[schedule_id]["availability"][user_id]["selected"][row["day_key"]] = row["time_value"]

    return schedules

def prune_old_schedules(max_count=10):
    """
    schedules 테이블에 저장된 스케줄이 max_count개를 초과하면
    가장 오래된 스케줄부터 완전 삭제한다.

    삭제 대상:
    - availability
    - schedules

    반환:
    - 삭제된 schedule_id 리스트
    """
    conn = get_connection()
    cur = conn.cursor()

    rows = cur.execute(
        """
        SELECT id
        FROM schedules
        ORDER BY id DESC
        """
    ).fetchall()

    if len(rows) <= max_count:
        conn.close()
        return []

    # 최신 max_count개는 유지하고, 나머지는 삭제
    keep_ids = [row["id"] for row in rows[:max_count]]
    delete_ids = [row["id"] for row in rows[max_count:]]

    for schedule_id in delete_ids:
        cur.execute(
            """
            DELETE FROM availability
            WHERE schedule_id = ?
            """,
            (schedule_id,),
        )

        cur.execute(
            """
            DELETE FROM schedules
            WHERE id = ?
            """,
            (schedule_id,),
        )

    conn.commit()
    conn.close()

    return delete_ids