import io
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from db import (
    init_db,
    load_active_schedules,
    create_schedule_record,
    update_schedule_message_id,
    save_availability,
    delete_availability_day,
    delete_availability_user,
    deactivate_schedule,
    prune_old_schedules,
    save_confirmed_schedules,
    clear_confirmed_schedules,
)

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_STR = os.getenv("DISCORD_GUILD_ID", "").strip()
GUILD_ID = int(GUILD_ID_STR) if GUILD_ID_STR.isdigit() else None

# 공대장 역할 설정
RAID_LEADER_ROLE_NAME = "공대장"
REQUIRE_LEADER_ROLE = False  # 실제 적용 시 True 권장

ANYTIME_VALUE = "ANYTIME"

MIN_RAID_MEMBER_COUNT = 6
MAX_VISIBLE_MEMBER_ROWS = 8
MAX_STORED_SCHEDULE_COUNT = 10

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# DB에서 로드되는 메모리 캐시
schedules = {}

KST = timezone(timedelta(hours=9))
MAX_MANUAL_SCHEDULE_DAYS = 14

def get_week_dates(base_date: Optional[datetime] = None):
    """
    수요일 시작 주간 생성
    예:
    수요일 ~ 다음 주 화요일
    """
    if base_date is None:
        base_date = datetime.now(KST)

    # Python weekday(): 월=0, 화=1, 수=2, 목=3 ...
    WEDNESDAY = 2

    days_since_wednesday = (base_date.weekday() - WEDNESDAY) % 7
    week_start = base_date - timedelta(days=days_since_wednesday)

    return [week_start + timedelta(days=i) for i in range(7)]


def get_manual_dates(start_text: str, end_text: str):
    """YYYY-MM-DD 형식의 시작일과 종료일로 날짜 목록을 만든다."""
    try:
        start_date = datetime.strptime(start_text, "%Y-%m-%d")
        end_date = datetime.strptime(end_text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("날짜는 `YYYY-MM-DD` 형식으로 입력해 주세요.") from exc

    if end_date < start_date:
        raise ValueError("종료일은 시작일과 같거나 이후여야 합니다.")

    day_count = (end_date - start_date).days + 1
    if day_count > MAX_MANUAL_SCHEDULE_DAYS:
        raise ValueError(f"수동 날짜 범위는 최대 {MAX_MANUAL_SCHEDULE_DAYS}일까지 지정할 수 있습니다.")

    return [start_date + timedelta(days=i) for i in range(day_count)]

def format_day_label(date_obj: datetime):
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return f"{date_obj.strftime('%m/%d')} ({weekdays[date_obj.weekday()]})"


def short_day_label(date_obj: datetime):
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return f"{date_obj.strftime('%m/%d')} {weekdays[date_obj.weekday()]}"


def is_raid_leader(member: discord.Member):
    if not REQUIRE_LEADER_ROLE:
        return True

    if member.guild_permissions.administrator:
        return True

    return any(role.name == RAID_LEADER_ROLE_NAME for role in member.roles)

def format_time_korean(time_str: str):
    """
    내부 저장값 HH:MM 또는 ANYTIME을 화면 표시용으로 변환
    예:
    ANYTIME -> 아무때나 가능
    09:00 -> 오전 09시 00분 이후
    21:30 -> 오후 09시 30분 이후
    """
    if time_str == ANYTIME_VALUE:
        return "아무때나 가능"

    hour_str, minute_str = time_str.split(":")
    hour = int(hour_str)
    minute = int(minute_str)

    if hour < 12:
        period = "오전"
        display_hour = 12 if hour == 0 else hour
    else:
        period = "오후"
        display_hour = 12 if hour == 12 else hour - 12

    return f"{period} {display_hour:02d}시 {minute:02d}분 이후"

def normalize_time_input(raw_text: str):
    """
    입력 예시:
    - 21
    - 21:30
    - 9시
    - 9시30분
    - 9시 30분
    - 오전9시
    - 오전 9시
    - 오전 9시 30분
    - 오후9시
    - 오후 9시
    - 오후 9시 30분
    - 오후 21시
    - 삭제 / 없음 / clear / x
    """
    text = (raw_text or "").strip().lower()

    if text == "":
        return {"action": "save", "value": ANYTIME_VALUE}

    if text in ["삭제", "없음", "clear", "remove", "x", "취소"]:
        return {"action": "clear"}

    # 오전/오후 확인
    period = None

    if "오전" in text:
        period = "am"
        text = text.replace("오전", "")

    if "오후" in text:
        period = "pm"
        text = text.replace("오후", "")

    # 영어 입력도 어느 정도 허용
    if "am" in text:
        period = "am"
        text = text.replace("am", "")

    if "pm" in text:
        period = "pm"
        text = text.replace("pm", "")

    text = text.replace(" ", "")
    text = text.replace("분", "")
    text = text.replace("시", ":")

    if text.endswith(":"):
        text += "00"

    if re.fullmatch(r"\d{1,2}", text):
        hour = int(text)
        minute = 0

    elif re.fullmatch(r"\d{1,2}:\d{1,2}", text):
        hour_str, minute_str = text.split(":")
        hour = int(hour_str)
        minute = int(minute_str)

    else:
        return {
            "action": "error",
            "message": (
                "시간 형식이 올바르지 않습니다. "
                "예: `21`, `21:30`, `오전 9시`, `오후 9시`, `오후 9시 30분`"
            )
        }

    if not (0 <= minute <= 59):
        return {
            "action": "error",
            "message": "분은 0~59 범위로 입력해주세요."
        }

    # 오전/오후 변환
    if period == "am":
        if not (1 <= hour <= 12):
            return {
                "action": "error",
                "message": "오전 시간은 1시~12시 형식으로 입력해주세요. 예: `오전 9시`, `오전 12시`"
            }

        if hour == 12:
            hour = 0

    elif period == "pm":
        if not (1 <= hour <= 23):
            return {
                "action": "error",
                "message": "오후 시간은 `오후 9시` 또는 `오후 21시`처럼 입력해주세요."
            }

        # 오후 1시~11시는 13~23시로 변환
        if 1 <= hour <= 11:
            hour += 12

        # 오후 12시는 그대로 12
        # 오후 13~23시는 그대로 허용

    else:
        # 오전/오후 없이 입력한 경우 기존처럼 24시간제로 처리
        if not (0 <= hour <= 23):
            return {
                "action": "error",
                "message": "시간은 0시~23시 범위로 입력해주세요."
            }

    return {"action": "save", "value": f"{hour:02d}:{minute:02d}"}


def rebuild_summary(schedule_id: int):
    schedule = schedules[schedule_id]
    summary = {}

    for day_key in schedule["days"].keys():
        summary[day_key] = []

    for _, user_data in schedule["availability"].items():
        user_name = user_data["name"]
        selected = user_data["selected"]

        for day_key, time_str in selected.items():
            summary[day_key].append({
                "name": user_name,
                "time": time_str
            })

    def sort_key(item):
        if item["time"] == ANYTIME_VALUE:
            return ("00:00", item["name"])
        return (item["time"], item["name"])

    for day_key in summary.keys():
        summary[day_key].sort(key=sort_key)

    schedule["summary"] = summary


def get_participant_count(schedule_id: int):
    schedule = schedules[schedule_id]
    count = 0

    for _, user_data in schedule["availability"].items():
        if user_data["selected"]:
            count += 1

    return count


def get_user_selected_time(schedule_id: int, user_id: int, day_key: str):
    schedule = schedules[schedule_id]
    user_data = schedule["availability"].get(str(user_id))
    if not user_data:
        return ""

    return user_data["selected"].get(day_key, "")

def time_value_to_minutes(time_value: str):
    if time_value == ANYTIME_VALUE:
        return 0

    hour_str, minute_str = time_value.split(":")
    return int(hour_str) * 60 + int(minute_str)


def minutes_to_time_value(minutes: int):
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}:{minute:02d}"


def get_active_users(schedule_id: int):
    schedule = schedules[schedule_id]

    active_users = {}
    for user_id, user_data in schedule["availability"].items():
        if user_data["selected"]:
            active_users[user_id] = user_data

    return active_users


def format_intersection_time(minutes: int):
    if minutes == 0:
        return "아무때나 가능"

    return format_time_korean(minutes_to_time_value(minutes))


def compute_intersections(schedule_id: int):
    """
    날짜별 진행 가능 시간 계산

    기준:
    - 해당 날짜에 가능 시간을 입력한 공대원이 MIN_RAID_MEMBER_COUNT명 이상이면 진행 가능
    - 진행 가능 시간은 그 인원들의 가능 시작 시간 중 가장 늦은 시간
    - ANYTIME은 00:00으로 계산
    """
    schedule = schedules[schedule_id]
    results = {}

    for day_key in schedule["days"].keys():
        available_members = []

        for _, user_data in schedule["availability"].items():
            selected = user_data["selected"]

            if day_key not in selected:
                continue

            available_members.append({
                "name": user_data["name"],
                "time": selected[day_key],
                "minutes": time_value_to_minutes(selected[day_key]),
            })

        available_count = len(available_members)

        if available_count < MIN_RAID_MEMBER_COUNT:
            results[day_key] = {
                "possible": False,
                "reason": "not_enough_members",
                "start_minutes": None,
                "display_text": f"{available_count}/{MIN_RAID_MEMBER_COUNT}명",
                "available_count": available_count,
                "available_names": [member["name"] for member in available_members],
            }
            continue

        intersection_start = max(member["minutes"] for member in available_members)

        results[day_key] = {
            "possible": True,
            "reason": None,
            "start_minutes": intersection_start,
            "display_text": format_intersection_time(intersection_start),
            "available_count": available_count,
            "available_names": [member["name"] for member in available_members],
        }

    return results


def build_possible_days_summary(schedule_id: int):
    schedule = schedules[schedule_id]
    intersections = compute_intersections(schedule_id)
    active_users = get_active_users(schedule_id)

    if not active_users:
        return "아직 시간을 입력한 공대원이 없어 계산할 수 없습니다."

    lines = []

    for day_key, day_label in schedule["days"].items():
        info = intersections[day_key]

        if info["possible"]:
            lines.append(f"• {day_label} - {info['display_text']}")

    if not lines:
        return "현재 모든 공대원이 공통으로 가능한 날짜가 없습니다."

    return "\n".join(lines)

def is_schedule_confirmed(schedule_id: int):
    schedule = schedules[schedule_id]
    return bool(schedule.get("is_confirmed")) and bool(schedule.get("confirmed_schedules"))


def build_confirmed_schedule_summary(schedule_id: int):
    schedule = schedules[schedule_id]
    confirmed_items = schedule.get("confirmed_schedules", [])

    if not confirmed_items:
        return "아직 확정된 일정이 없습니다."

    lines = []
    for item in confirmed_items:
        lines.append(f"• {item['label']}")

    return "\n".join(lines)


def build_schedule_status_summary(schedule_id: int):
    if is_schedule_confirmed(schedule_id):
        return build_confirmed_schedule_summary(schedule_id)

    return build_possible_days_summary(schedule_id)


async def confirm_schedule_by_items(schedule_id: int, confirmed_items: list[dict]):
    if schedule_id not in schedules:
        return False, "활성 스케줄을 찾을 수 없습니다."

    if not confirmed_items:
        return False, "확정할 일정이 없습니다."

    schedule = schedules[schedule_id]

    schedule["is_confirmed"] = 1
    schedule["confirmed_schedules"] = confirmed_items

    save_confirmed_schedules(schedule_id, confirmed_items)

    await update_schedule_message(schedule_id)

    return True, build_confirmed_schedule_summary(schedule_id)

def load_font(size: int, bold: bool = False):
    """
    Linux/WSL에서 한글 표시 가능한 폰트를 우선 사용
    필요 시: sudo apt install fonts-nanum -y
    """
    font_candidates = []

    if bold:
        font_candidates.extend([
            "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ])
    else:
        font_candidates.extend([
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ])

    for path in font_candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)

    return ImageFont.load_default()

def make_calendar_file(schedule_id: int):
    schedule = schedules[schedule_id]
    week_dates = schedule["week_dates"]
    summary = schedule["summary"]
    intersections = compute_intersections(schedule_id)
    possible_days_summary = build_schedule_status_summary(schedule_id)

    possible_lines = possible_days_summary.split("\n")

    # 이미지 크기
    width = 2100

    # 상단 안내 박스 제거
    top_area_height = 120

    # 공대원 입력 현황 최대 8명 기준으로 높이 확보
    cell_height = 560

    # 레이드 진행 가능일 박스 높이 자동 계산
    summary_line_height = 34
    summary_box_base_height = 130
    summary_box_height = max(
        250,
        summary_box_base_height + len(possible_lines) * summary_line_height
    )

    footer_height = 50
    calendar_top = top_area_height
    calendar_row_gap = 28
    calendar_row_count = max(1, (len(week_dates) + 6) // 7)
    calendar_height = cell_height * calendar_row_count + calendar_row_gap * (calendar_row_count - 1)
    summary_top = calendar_top + calendar_height + 28
    height = summary_top + summary_box_height + footer_height

    # 색상
    bg = (247, 249, 252)
    white = (255, 255, 255)
    border = (210, 216, 224)
    header_bg = (233, 238, 245)
    title_color = (35, 39, 42)
    sub_color = (90, 98, 109)
    green = (49, 163, 84)
    gray_text = (110, 120, 130)
    line_color = (225, 230, 236)

    possible_bg = (232, 247, 236)
    possible_border = (82, 173, 107)
    disabled_bg = (255, 244, 229)
    disabled_border = (235, 162, 78)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # 폰트
    title_font = load_font(34, bold=True)
    sub_font = load_font(21, bold=False)
    sub_bold_font = load_font(21, bold=True)
    day_font = load_font(24, bold=True)
    day_sub_font = load_font(20, bold=False)
    count_font = load_font(20, bold=True)
    item_font = load_font(17, bold=False)
    small_font = load_font(17, bold=False)
    box_title_font = load_font(22, bold=True)
    box_value_font = load_font(19, bold=True)

    # 상단 타이틀
    draw.text(
        (40, 24),
        f"{schedule['title']} 스케줄",
        font=title_font,
        fill=title_color
    )

    draw.text(
        (40, 72),
        (
            f"대상 기간: {schedule['week_start_label']} ~ {schedule['week_end_label']}"
            f"   |   참여 인원: {get_participant_count(schedule_id)}명"
            f"   |   진행 가능 기준: {MIN_RAID_MEMBER_COUNT}명 이상"
        ),
        font=sub_font,
        fill=sub_color
    )

    # 주간 달력 영역
    left_margin = 30
    right_margin = 30
    gap = 14
    column_count = min(7, max(1, len(week_dates)))
    cell_width = (width - left_margin - right_margin - gap * (column_count - 1)) // column_count

    for idx, date_obj in enumerate(week_dates):
        column = idx % 7
        row = idx // 7
        x1 = left_margin + column * (cell_width + gap)
        y1 = calendar_top + row * (cell_height + calendar_row_gap)
        x2 = x1 + cell_width
        y2 = y1 + cell_height

        day_key = date_obj.strftime("%Y-%m-%d")
        weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
        weekday = weekday_names[date_obj.weekday()]
        entries = summary.get(day_key, [])
        intersection = intersections[day_key]

        # 카드 외곽선
        card_outline = possible_border if intersection["possible"] else border

        draw.rounded_rectangle(
            (x1, y1, x2, y2),
            radius=18,
            fill=white,
            outline=card_outline,
            width=3 if intersection["possible"] else 2
        )

        # 카드 헤더
        header_h = 78
        draw.rounded_rectangle(
            (x1, y1, x2, y1 + header_h),
            radius=18,
            fill=header_bg,
            outline=header_bg
        )
        draw.rectangle((x1, y1 + 18, x2, y1 + header_h), fill=header_bg)

        draw.text(
            (x1 + 18, y1 + 14),
            date_obj.strftime("%m/%d"),
            font=day_font,
            fill=title_color
        )
        draw.text(
            (x1 + 18, y1 + 44),
            f"{weekday}요일",
            font=day_sub_font,
            fill=sub_color
        )

        count_text = f"{len(entries)}명 입력" if entries else "선택 없음"
        count_fill = green if entries else gray_text

        count_bbox = draw.textbbox((0, 0), count_text, font=count_font)
        count_w = count_bbox[2] - count_bbox[0]

        draw.text(
            (x2 - count_w - 18, y1 + 26),
            count_text,
            font=count_font,
            fill=count_fill
        )

        # 진행 가능 시간 박스
        inter_box_top = y1 + header_h + 14
        inter_box_bottom = inter_box_top + 86

        ##confirmed_day_key = schedule.get("confirmed_day_key")
        
        confirmed_items_for_day = [
            item
            for item in schedule.get("confirmed_schedules", [])
            if item["day_key"] == day_key
        ]

        if is_schedule_confirmed(schedule_id) and confirmed_items_for_day:
            confirmed_item = confirmed_items_for_day[0]

            inter_fill = possible_bg
            inter_outline = possible_border
            inter_title = "확정 일정"

            if confirmed_item["time_value"] == ANYTIME_VALUE:
                inter_value = "아무때나 가능"
            elif confirmed_item["time_value"] == "직접 입력":
                inter_value = "직접 입력"
            else:
                inter_value = format_time_korean(confirmed_item["time_value"])

            inter_sub = "레이드 진행 예정일"

        elif intersection["possible"]:
            inter_fill = possible_bg
            inter_outline = possible_border
            inter_title = "진행 가능 시간"
            inter_value = intersection["display_text"]
            inter_sub = f"{intersection['available_count']}명 가능"

        else:
            inter_fill = disabled_bg
            inter_outline = disabled_border
            inter_title = "진행 불가"
            inter_value = f"{intersection['available_count']}/{MIN_RAID_MEMBER_COUNT}명"
            inter_sub = f"{MIN_RAID_MEMBER_COUNT}명 이상 필요"

        draw.rounded_rectangle(
            (x1 + 14, inter_box_top, x2 - 14, inter_box_bottom),
            radius=14,
            fill=inter_fill,
            outline=inter_outline,
            width=2
        )

        draw.text(
            (x1 + 28, inter_box_top + 10),
            inter_title,
            font=box_title_font,
            fill=title_color
        )

        draw.text(
            (x1 + 28, inter_box_top + 38),
            inter_value,
            font=box_value_font,
            fill=title_color
        )

        draw.text(
            (x1 + 28, inter_box_top + 62),
            inter_sub,
            font=small_font,
            fill=sub_color
        )

        # 공대원 입력 현황
        content_top = inter_box_bottom + 18
        content_left = x1 + 16
        content_right = x2 - 16

        draw.text(
            (content_left, content_top),
            "공대원 입력 현황",
            font=sub_bold_font,
            fill=title_color
        )

        list_top = content_top + 34

        if not entries:
            draw.text(
                (content_left, list_top + 6),
                "아직 입력한 공대원이 없습니다.",
                font=item_font,
                fill=gray_text
            )
        else:
            row_h = 30
            row_gap = 6
            max_visible = MAX_VISIBLE_MEMBER_ROWS

            for item_idx, item in enumerate(entries[:max_visible]):
                row_top = list_top + item_idx * (row_h + row_gap)
                row_bottom = row_top + row_h

                draw.rounded_rectangle(
                    (content_left, row_top, content_right, row_bottom),
                    radius=10,
                    fill=(245, 247, 250),
                    outline=line_color,
                    width=1
                )

                line_text = f"{format_time_korean(item['time'])} - {item['name']}"
                draw.text(
                    (content_left + 10, row_top + 6),
                    line_text,
                    font=item_font,
                    fill=title_color
                )

            if len(entries) > max_visible:
                more_text = f"... 외 {len(entries) - max_visible}명"
                draw.text(
                    (content_left, y2 - 30),
                    more_text,
                    font=small_font,
                    fill=gray_text
                )

    # 하단 레이드 진행 가능일 박스
    summary_bottom = summary_top + summary_box_height

    draw.rounded_rectangle(
        (32, summary_top, width - 32, summary_bottom),
        radius=18,
        fill=white,
        outline=border,
        width=2
    )

    summary_title = "레이드 진행 예정일" if is_schedule_confirmed(schedule_id) else "레이드 진행 가능일"

    draw.text(
        (54, summary_top + 18),
        summary_title,
        font=title_font,
        fill=title_color
    )

    summary_description = (
        "확정된 레이드 진행 예정일입니다."
        if is_schedule_confirmed(schedule_id)
        else f"{MIN_RAID_MEMBER_COUNT}명 이상 공통으로 가능한 날짜와 시작 가능 시간입니다."
    )

    draw.text(
        (54, summary_top + 64),
        summary_description,
        font=sub_font,
        fill=sub_color
    )
    
    text_y = summary_top + 108

    if f"{MIN_RAID_MEMBER_COUNT}명 이상 공통으로 가능한 날짜가 없습니다" in possible_days_summary:
        draw.text(
            (58, text_y),
            possible_days_summary,
            font=sub_bold_font,
            fill=gray_text
        )
    else:
        for line in possible_lines:
            draw.text(
                (58, text_y),
                line,
                font=sub_bold_font,
                fill=title_color
            )
            text_y += summary_line_height

    # 하단 설명
    footer_text = "※ 이미지 갱신 시 최신 참석 가능 시간이 반영됩니다."
    draw.text(
        (36, height - 34),
        footer_text,
        font=small_font,
        fill=gray_text
    )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return discord.File(fp=buffer, filename="schedule_calendar.png")


def build_schedule_embed(schedule_id: int):
    schedule = schedules[schedule_id]
    schedule_status_summary = build_schedule_status_summary(schedule_id)

    if is_schedule_confirmed(schedule_id):
        description = (
            "이 스케줄은 **확정된 스케줄**입니다.\n"
            "더 이상 주간 일정 입력을 받지 않습니다."
        )
    else:
        description = (
            "아래 버튼을 눌러 해당 기간에 참석 가능한 **날짜**를 고른 뒤,\n"
            "**그 날 몇 시 이후 가능한지** 입력하세요.\n\n"
            "입력 예시: `21`, `21:30`, `오전 9시`, `오후 9시`, `오후 9시 30분`\n"
            "공란 제출: `아무때나 가능`\n"
            "삭제 예시: `삭제` / `없음`\n\n"
            f"※ 레이드 진행 가능일은 **{MIN_RAID_MEMBER_COUNT}명 이상** 가능한 날짜만 표시됩니다."
        )

    embed = discord.Embed(
        title=f"📅 {schedule['title']} 스케줄",
        description=description,
        color=discord.Color.green() if is_schedule_confirmed(schedule_id) else discord.Color.blue(),
    )

    embed.add_field(name="생성자", value=schedule["creator_name"], inline=True)
    embed.add_field(
        name="대상 기간",
        value=f"{schedule['week_start_label']} ~ {schedule['week_end_label']}",
        inline=True,
    )
    embed.add_field(
        name="참여 인원",
        value=f"{get_participant_count(schedule_id)}명",
        inline=True,
    )

    embed.add_field(
        name="레이드 진행 예정일" if is_schedule_confirmed(schedule_id) else "레이드 진행 가능일",
        value=schedule_status_summary,
        inline=False,
    )

    embed.set_image(url="attachment://schedule_calendar.png")
    embed.set_footer(text=f"Schedule ID: {schedule_id}")

    return embed

def build_deleted_schedule_embed(schedule):
    embed = discord.Embed(
        title=f"🗑️ {schedule['title']} 스케줄 삭제됨",
        description=(
            "이 스케줄은 공대장에 의해 삭제되었습니다.\n"
            "더 이상 주간 일정 입력을 받을 수 없습니다."
        ),
        color=discord.Color.dark_gray(),
    )

    embed.add_field(
        name="생성자",
        value=schedule["creator_name"],
        inline=True,
    )

    embed.add_field(
        name="대상 주간",
        value=f"{schedule['week_start_label']} ~ {schedule['week_end_label']}",
        inline=True,
    )

    embed.add_field(
        name="Schedule ID",
        value=str(schedule["id"]),
        inline=True,
    )

    return embed

async def deactivate_schedule_by_id(schedule_id: int):
    if schedule_id not in schedules:
        return False, "활성 스케줄을 찾을 수 없습니다."

    schedule = schedules[schedule_id]

    # DB에서 비활성화
    deactivate_schedule(schedule_id)

    # 기존 디스코드 스케줄 메시지 수정
    try:
        channel = bot.get_channel(schedule["channel_id"])

        if channel is None:
            channel = await bot.fetch_channel(schedule["channel_id"])

        if schedule.get("message_id"):
            message = await channel.fetch_message(schedule["message_id"])

            await message.edit(
                embed=build_deleted_schedule_embed(schedule),
                attachments=[],
                view=None,
            )

    except Exception as e:
        print(f"Failed to update deleted schedule message: {e}")

    # 메모리 캐시에서 제거
    schedules.pop(schedule_id, None)

    return True, schedule["title"]

async def update_schedule_message(schedule_id: int):
    schedule = schedules[schedule_id]

    channel = bot.get_channel(schedule["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(schedule["channel_id"])
        except Exception as e:
            print(f"Failed to fetch channel: {e}")
            return

    try:
        message = await channel.fetch_message(schedule["message_id"])
        embed = build_schedule_embed(schedule_id)
        calendar_file = make_calendar_file(schedule_id)

        view = None if is_schedule_confirmed(schedule_id) else ScheduleMainView(schedule_id)

        await message.edit(
            embed=build_schedule_embed(schedule_id),
            attachments=[calendar_file],
            view=view,
        )
    except Exception as e:
        print(f"Failed to update schedule message: {e}")


class AvailableAfterModal(discord.ui.Modal):
    def __init__(self, schedule_id: int, day_key: str, current_value: str = ""):
        self.schedule_id = schedule_id
        self.day_key = day_key

        schedule = schedules[schedule_id]

        if day_key == "__ALL__":
            day_label = "전부 가능"
        else:
            day_label = schedule["days"][day_key]

        super().__init__(title=f"{day_label} 가능 시간 입력")

        if current_value == ANYTIME_VALUE:
            display_default = ""
        else:
            display_default = current_value

        self.time_input = discord.ui.TextInput(
            label="몇 시 이후 가능한가요?",
            placeholder="공란=아무때나 가능 / 예: 오후 9시 / 삭제",
            default=display_default,
            required=False,
            max_length=20,
        )
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw_value = str(self.time_input.value or "").strip()
        parsed = normalize_time_input(raw_value)

        if parsed["action"] == "error":
            await interaction.response.send_message(
                f"❌ {parsed['message']}",
                ephemeral=True,
            )
            return

        schedule = schedules[self.schedule_id]
        user_id = str(interaction.user.id)
        user_name = interaction.user.display_name

        if user_id not in schedule["availability"]:
            schedule["availability"][user_id] = {
                "name": user_name,
                "selected": {}
            }

        schedule["availability"][user_id]["name"] = user_name

        if self.day_key == "__ALL__":
            day_label = "전체 날짜"
        else:
            day_label = schedule["days"][self.day_key]

        if parsed["action"] == "clear":
            if self.day_key == "__ALL__":
                for day_key in schedule["days"].keys():
                    schedule["availability"][user_id]["selected"].pop(day_key, None)
                    delete_availability_day(self.schedule_id, user_id, day_key)
            else:
                schedule["availability"][user_id]["selected"].pop(self.day_key, None)
                delete_availability_day(self.schedule_id, user_id, self.day_key)

            rebuild_summary(self.schedule_id)
            await update_schedule_message(self.schedule_id)

            await interaction.response.send_message(
                f"🗑️ `{day_label}` 일정 선택을 삭제했습니다.",
                ephemeral=True,
            )
            return

        if parsed["action"] == "save":
            saved_time = parsed["value"]

            if self.day_key == "__ALL__":
                for day_key in schedule["days"].keys():
                    schedule["availability"][user_id]["selected"][day_key] = saved_time

                    save_availability(
                        self.schedule_id,
                        user_id,
                        user_name,
                        day_key,
                        saved_time,
                    )
            else:
                schedule["availability"][user_id]["selected"][self.day_key] = saved_time

                save_availability(
                    self.schedule_id,
                    user_id,
                    user_name,
                    self.day_key,
                    saved_time,
                )

            rebuild_summary(self.schedule_id)
            await update_schedule_message(self.schedule_id)

            await interaction.response.send_message(
                f"✅ `{day_label}`에 대해 **{format_time_korean(saved_time)}** 으로 저장했습니다.",
                ephemeral=True,
            )
            return

class AllDaysButton(discord.ui.Button):
    def __init__(self, schedule_id: int, row: int = 2):
        super().__init__(
            label="전부 가능",
            style=discord.ButtonStyle.success,
            row=row,
        )
        self.schedule_id = schedule_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            AvailableAfterModal(
                schedule_id=self.schedule_id,
                day_key="__ALL__",
                current_value=""
            )
        )

class DayButton(discord.ui.Button):
    def __init__(self, schedule_id: int, day_key: str, label: str, row: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            row=row,
        )
        self.schedule_id = schedule_id
        self.day_key = day_key

    async def callback(self, interaction: discord.Interaction):
        current_value = get_user_selected_time(
            self.schedule_id,
            interaction.user.id,
            self.day_key
        )

        await interaction.response.send_modal(
            AvailableAfterModal(
                schedule_id=self.schedule_id,
                day_key=self.day_key,
                current_value=current_value
            )
        )


class ResetMyScheduleButton(discord.ui.Button):
    def __init__(self, schedule_id: int, row: int = 2):
        super().__init__(
            label="내 일정 전체 삭제",
            style=discord.ButtonStyle.danger,
            row=row,
        )
        self.schedule_id = schedule_id

    async def callback(self, interaction: discord.Interaction):
        schedule = schedules[self.schedule_id]
        user_id = str(interaction.user.id)

        if user_id in schedule["availability"]:
            schedule["availability"][user_id]["selected"] = {}
            delete_availability_user(self.schedule_id, user_id)

            rebuild_summary(self.schedule_id)
            await update_schedule_message(self.schedule_id)

        await interaction.response.send_message(
            "🗑️ 내 일정 선택을 모두 삭제했습니다.",
            ephemeral=True,
        )


class DaySelectView(discord.ui.View):
    def __init__(self, schedule_id: int):
        super().__init__(timeout=300)
        self.schedule_id = schedule_id

        schedule = schedules[schedule_id]
        day_items = list(schedule["days"].items())

        for idx, (day_key, _) in enumerate(day_items):
            date_obj = datetime.strptime(day_key, "%Y-%m-%d")
            label = short_day_label(date_obj)
            row = idx // 5
            self.add_item(DayButton(schedule_id, day_key, label, row=row))

        action_row = (len(day_items) + 4) // 5
        self.add_item(AllDaysButton(schedule_id, row=action_row))
        self.add_item(ResetMyScheduleButton(schedule_id, row=action_row))

class OpenWeekInputButton(discord.ui.Button):
    def __init__(self, schedule_id: int):
        super().__init__(
            label="주간 일정 입력하기",
            style=discord.ButtonStyle.success,
            emoji="🗓️",
            custom_id=f"open_week_input:{schedule_id}",
        )
        self.schedule_id = schedule_id

    async def callback(self, interaction: discord.Interaction):
        if self.schedule_id not in schedules:
            await interaction.response.send_message(
                "이미 만료되었거나 찾을 수 없는 스케줄입니다.",
                ephemeral=True,
            )
            return

        schedule = schedules[self.schedule_id]

        await interaction.response.send_message(
            f"🗓️ `{schedule['title']}` 에 대해 날짜를 고르고, 몇 시 이후 가능한지 입력하세요.",
            view=DaySelectView(self.schedule_id),
            ephemeral=True,
        )

class ConfirmPossibleDaySelect(discord.ui.Select):
    def __init__(self, schedule_id: int):
        self.schedule_id = schedule_id
        schedule = schedules[schedule_id]
        intersections = compute_intersections(schedule_id)

        options = []

        for day_key, day_label in schedule["days"].items():
            info = intersections[day_key]

            if not info["possible"]:
                continue

            options.append(
                discord.SelectOption(
                    label=day_label,
                    value=day_key,
                    description=info["display_text"],
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="진행 가능일 없음",
                    value="__NONE__",
                    description="직접 입력 버튼을 사용하세요.",
                )
            )

        max_values = 1 if options[0].value == "__NONE__" else len(options)

        super().__init__(
            placeholder="레이드 진행 가능일을 하나 이상 선택하세요.",
            min_values=1,
            max_values=max_values,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected_day_keys = self.values

        if "__NONE__" in selected_day_keys:
            await interaction.response.send_message(
                "현재 선택 가능한 레이드 진행 가능일이 없습니다. 직접 입력을 사용하세요.",
                ephemeral=True,
            )
            return

        schedule = schedules[self.schedule_id]
        intersections = compute_intersections(self.schedule_id)

        confirmed_items = []

        for selected_day_key in selected_day_keys:
            info = intersections[selected_day_key]

            if not info["possible"]:
                continue

            start_minutes = info["start_minutes"]

            if start_minutes == 0:
                confirmed_time_value = ANYTIME_VALUE
            else:
                confirmed_time_value = minutes_to_time_value(start_minutes)

            confirmed_label = (
                f"{schedule['days'][selected_day_key]} - "
                f"{format_intersection_time(start_minutes)}"
            )

            confirmed_items.append(
                {
                    "day_key": selected_day_key,
                    "time_value": confirmed_time_value,
                    "label": confirmed_label,
                }
            )

        success, result = await confirm_schedule_by_items(
            schedule_id=self.schedule_id,
            confirmed_items=confirmed_items,
        )

        if not success:
            await interaction.response.send_message(
                f"확정 실패: {result}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ `{schedule['title']}` 스케줄을 확정했습니다.\n{result}",
            ephemeral=True,
        )


class ManualConfirmModal(discord.ui.Modal):
    def __init__(self, schedule_id: int):
        self.schedule_id = schedule_id

        schedule = schedules[schedule_id]

        super().__init__(title=f"{schedule['title']} 직접 확정")

        self.confirm_text = discord.ui.TextInput(
            label="확정할 일정을 직접 입력하세요.",
            placeholder="예:\n06/01 (월) 오후 9시\n06/03 (수) 오후 10시",
            required=True,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.confirm_text)

    async def on_submit(self, interaction: discord.Interaction):
        raw_text = str(self.confirm_text.value or "").strip()

        if not raw_text:
            await interaction.response.send_message(
                "확정할 일정을 입력해주세요.",
                ephemeral=True,
            )
            return

        lines = [
            line.strip()
            for line in raw_text.splitlines()
            if line.strip()
        ]

        confirmed_items = []

        for idx, line in enumerate(lines):
            confirmed_items.append(
                {
                    "day_key": f"__MANUAL__{idx + 1}",
                    "time_value": "직접 입력",
                    "label": line,
                }
            )

        success, result = await confirm_schedule_by_items(
            schedule_id=self.schedule_id,
            confirmed_items=confirmed_items,
        )

        if not success:
            await interaction.response.send_message(
                f"확정 실패: {result}",
                ephemeral=True,
            )
            return

        schedule = schedules[self.schedule_id]

        await interaction.response.send_message(
            f"✅ `{schedule['title']}` 스케줄을 직접 입력한 일정으로 확정했습니다.\n{result}",
            ephemeral=True,
        )


class ManualConfirmButton(discord.ui.Button):
    def __init__(self, schedule_id: int):
        super().__init__(
            label="직접 입력으로 확정",
            style=discord.ButtonStyle.secondary,
        )
        self.schedule_id = schedule_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            ManualConfirmModal(self.schedule_id)
        )


class ConfirmScheduleView(discord.ui.View):
    def __init__(self, schedule_id: int):
        super().__init__(timeout=300)
        self.schedule_id = schedule_id
        self.add_item(ConfirmPossibleDaySelect(schedule_id))
        self.add_item(ManualConfirmButton(schedule_id))


class ScheduleConfirmButton(discord.ui.Button):
    def __init__(self, schedule_id: int, schedule_title: str):
        super().__init__(
            label=f"확정: {schedule_title}",
            style=discord.ButtonStyle.success,
            custom_id=f"confirm_schedule:{schedule_id}",
        )
        self.schedule_id = schedule_id
        self.schedule_title = schedule_title

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "서버 안에서만 사용할 수 있는 기능입니다.",
                ephemeral=True,
            )
            return

        if not is_raid_leader(interaction.user):
            await interaction.response.send_message(
                "공대장만 스케줄을 확정할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.schedule_id not in schedules:
            await interaction.response.send_message(
                "이미 삭제되었거나 찾을 수 없는 스케줄입니다.",
                ephemeral=True,
            )
            return

        if is_schedule_confirmed(self.schedule_id):
            await interaction.response.send_message(
                "이미 확정된 스케줄입니다.",
                ephemeral=True,
            )
            return

        schedule = schedules[self.schedule_id]

        await interaction.response.send_message(
            f"✅ `{schedule['title']}` 스케줄을 확정할 방법을 선택하세요.",
            view=ConfirmScheduleView(self.schedule_id),
            ephemeral=True,
        )

class ScheduleDeleteButton(discord.ui.Button):
    def __init__(self, schedule_id: int, schedule_title: str):
        super().__init__(
            label=f"삭제: {schedule_title}",
            style=discord.ButtonStyle.danger,
            custom_id=f"delete_schedule:{schedule_id}",
        )
        self.schedule_id = schedule_id
        self.schedule_title = schedule_title

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "서버 안에서만 사용할 수 있는 기능입니다.",
                ephemeral=True,
            )
            return

        if not is_raid_leader(interaction.user):
            await interaction.response.send_message(
                "공대장만 스케줄을 삭제할 수 있습니다.",
                ephemeral=True,
            )
            return

        if self.schedule_id not in schedules:
            await interaction.response.send_message(
                "이미 삭제되었거나 찾을 수 없는 스케줄입니다.",
                ephemeral=True,
            )
            return

        success, result = await deactivate_schedule_by_id(self.schedule_id)

        if not success:
            await interaction.response.send_message(
                f"삭제 실패: {result}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🗑️ Schedule ID `{self.schedule_id}` - `{result}` 스케줄을 삭제했습니다.",
            ephemeral=True,
        )


class ScheduleListView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

        for schedule_id, schedule in sorted(schedules.items()):
            if not is_schedule_confirmed(schedule_id):
                self.add_item(
                    ScheduleConfirmButton(
                        schedule_id=schedule_id,
                        schedule_title=schedule["title"],
                    )
                )

            self.add_item(
                ScheduleDeleteButton(
                    schedule_id=schedule_id,
                    schedule_title=schedule["title"],
                )
            )

class ScheduleMainView(discord.ui.View):
    def __init__(self, schedule_id: int):
        super().__init__(timeout=None)
        self.schedule_id = schedule_id
        self.add_item(OpenWeekInputButton(schedule_id))


@bot.event
async def on_ready():
    global schedules

    print(f"Logged in as {bot.user}")

    try:
        init_db()

        deleted_schedule_ids = prune_old_schedules(MAX_STORED_SCHEDULE_COUNT)

        if deleted_schedule_ids:
            print(f"Pruned old schedule(s) from SQLite on startup: {deleted_schedule_ids}")

        schedules = load_active_schedules()

        for schedule_id in schedules.keys():
            rebuild_summary(schedule_id)

            if not is_schedule_confirmed(schedule_id):
                bot.add_view(ScheduleMainView(schedule_id))

        print(f"Loaded {len(schedules)} active schedule(s) from SQLite")

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} command(s)")

    except Exception as e:
        print(f"Failed to initialize bot: {e}")


async def create_schedule_for_dates(
    interaction: discord.Interaction,
    title: str,
    schedule_dates,
    period_value: str,
):
    week_start = schedule_dates[0]
    week_end = schedule_dates[-1]

    # Discord 인터랙션은 3초 안에 응답해야 하므로 이미지 생성 전에 먼저 승인한다.
    await interaction.response.defer(thinking=True)

    print(
        "Creating schedule: "
        f"period={period_value}, "
        f"range={week_start.strftime('%Y-%m-%d')}~{week_end.strftime('%Y-%m-%d')}"
    )

    days = {}
    for date_obj in schedule_dates:
        day_key = date_obj.strftime("%Y-%m-%d")
        days[day_key] = format_day_label(date_obj)

    temp_schedule = {
        "title": title,
        "creator_id": interaction.user.id,
        "creator_name": interaction.user.display_name,
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "message_id": None,
        "week_start": week_start.strftime("%Y-%m-%d"),
        "week_end": week_end.strftime("%Y-%m-%d"),
    }

    schedule_id = create_schedule_record(temp_schedule)
    deleted_schedule_ids = prune_old_schedules(MAX_STORED_SCHEDULE_COUNT)

    for deleted_schedule_id in deleted_schedule_ids:
        schedules.pop(deleted_schedule_id, None)

    if deleted_schedule_ids:
        print(f"Pruned old schedule(s) from SQLite: {deleted_schedule_ids}")

    schedules[schedule_id] = {
        "id": schedule_id,
        "title": title,
        "creator_id": interaction.user.id,
        "creator_name": interaction.user.display_name,
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "message_id": None,
        "week_start": week_start.strftime("%Y-%m-%d"),
        "week_end": week_end.strftime("%Y-%m-%d"),
        "week_dates": schedule_dates,
        "week_start_label": format_day_label(week_start),
        "week_end_label": format_day_label(week_end),
        "days": days,
        "availability": {},
        "summary": {},
        "is_confirmed": 0,
        "confirmed_schedules": [],
    }

    rebuild_summary(schedule_id)

    embed = build_schedule_embed(schedule_id)
    calendar_file = make_calendar_file(schedule_id)
    view = ScheduleMainView(schedule_id)

    message = await interaction.edit_original_response(
        embed=embed,
        attachments=[calendar_file],
        view=view,
    )

    schedules[schedule_id]["message_id"] = message.id
    update_schedule_message_id(schedule_id, message.id)
    bot.add_view(ScheduleMainView(schedule_id))


@bot.tree.command(
    name="스케줄생성",
    description="수요일 기준으로 레이드 주간 스케줄을 생성합니다.",
)
@app_commands.describe(
    제목="예: 침식레이드, 하멘, 베히모스",
    기간="이번 주, 다음 주 또는 다다음 주를 선택합니다.",
)
@app_commands.choices(
    기간=[
        app_commands.Choice(name="이번 주", value="current"),
        app_commands.Choice(name="다음 주", value="next"),
        app_commands.Choice(name="다다음 주", value="week_after_next"),
    ]
)
async def create_schedule(
    interaction: discord.Interaction,
    제목: str,
    기간: app_commands.Choice[str] = None,
):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "서버 안에서만 사용할 수 있는 명령어입니다.",
            ephemeral=True,
        )
        return

    if not is_raid_leader(interaction.user):
        await interaction.response.send_message(
            "공대장만 스케줄을 생성할 수 있습니다.",
            ephemeral=True,
        )
        return

    period_value = 기간.value if 기간 else "current"
    week_offset = {
        "current": 0,
        "next": 1,
        "week_after_next": 2,
    }[period_value]
    week_dates = get_week_dates(datetime.now(KST) + timedelta(weeks=week_offset))

    await create_schedule_for_dates(interaction, 제목, week_dates, period_value)


@bot.tree.command(
    name="스케줄수동생성",
    description="시작일과 종료일을 직접 지정하여 스케줄을 생성합니다.",
)
@app_commands.describe(
    제목="예: 침식레이드, 하멘, 베히모스",
    시작일="시작일 (YYYY-MM-DD)",
    종료일="종료일 (YYYY-MM-DD, 최대 14일)",
)
async def create_manual_schedule(
    interaction: discord.Interaction,
    제목: str,
    시작일: str,
    종료일: str,
):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "서버 안에서만 사용할 수 있는 명령어입니다.",
            ephemeral=True,
        )
        return

    if not is_raid_leader(interaction.user):
        await interaction.response.send_message(
            "공대장만 스케줄을 생성할 수 있습니다.",
            ephemeral=True,
        )
        return

    try:
        schedule_dates = get_manual_dates(시작일, 종료일)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await create_schedule_for_dates(interaction, 제목, schedule_dates, "manual")

@bot.tree.command(
    name="스케줄목록",
    description="현재 활성화된 레이드 스케줄 목록을 확인합니다.",
)
async def list_schedules(interaction: discord.Interaction):
    if not schedules:
        await interaction.response.send_message(
            "현재 활성화된 스케줄이 없습니다.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="📋 활성 스케줄 목록",
        description=(
            "현재 활성화된 스케줄입니다.\n"
            "공대장은 아래 삭제 버튼으로 스케줄을 삭제할 수 있습니다."
        ),
        color=discord.Color.blue(),
    )

    for schedule_id, schedule in sorted(schedules.items()):
        participant_count = get_participant_count(schedule_id)
        schedule_status_summary = build_schedule_status_summary(schedule_id)

        if len(schedule_status_summary) > 250:
            schedule_status_summary = schedule_status_summary[:250] + "\n..."

        status_title = "레이드 진행 예정일" if is_schedule_confirmed(schedule_id) else "레이드 진행 가능일"

        value = (
            f"**Schedule ID:** `{schedule_id}`\n"
            f"**대상 주간:** {schedule['week_start_label']} ~ {schedule['week_end_label']}\n"
            f"**참여 인원:** {participant_count}명\n"
            f"**상태:** {'확정됨' if is_schedule_confirmed(schedule_id) else '입력 중'}\n"
            f"**{status_title}:**\n{schedule_status_summary}"
        )

        embed.add_field(
            name=f"📅 {schedule['title']}",
            value=value,
            inline=False,
        )

    await interaction.response.send_message(
        embed=embed,
        view=ScheduleListView(),
        ephemeral=True,
    )

# @bot.tree.command(
#     name="스케줄삭제",
#     description="지정한 레이드 스케줄을 삭제합니다.",
# )
# @app_commands.describe(
#     스케줄번호="삭제할 스케줄 번호입니다. /스케줄목록에서 확인할 수 있습니다."
# )
# async def delete_schedule(
#     interaction: discord.Interaction,
#     스케줄번호: int,
# ):
#     if not isinstance(interaction.user, discord.Member):
#         await interaction.response.send_message(
#             "서버 안에서만 사용할 수 있는 명령어입니다.",
#             ephemeral=True,
#         )
#         return

#     if not is_raid_leader(interaction.user):
#         await interaction.response.send_message(
#             "공대장만 스케줄을 삭제할 수 있습니다.",
#             ephemeral=True,
#         )
#         return

#     schedule_id = 스케줄번호

#     if schedule_id not in schedules:
#         await interaction.response.send_message(
#             f"Schedule ID `{schedule_id}` 에 해당하는 활성 스케줄을 찾을 수 없습니다.",
#             ephemeral=True,
#         )
#         return

#     schedule = schedules[schedule_id]

#     # DB에서 비활성화
#     deactivate_schedule(schedule_id)

#     # 기존 디스코드 메시지 버튼 제거 및 삭제 표시
#     try:
#         channel = bot.get_channel(schedule["channel_id"])

#         if channel is None:
#             channel = await bot.fetch_channel(schedule["channel_id"])

#         if schedule.get("message_id"):
#             message = await channel.fetch_message(schedule["message_id"])

#             await message.edit(
#                 embed=build_deleted_schedule_embed(schedule),
#                 attachments=[],
#                 view=None,
#             )

#     except Exception as e:
#         print(f"Failed to update deleted schedule message: {e}")

#     # 메모리 캐시에서 제거
#     schedules.pop(schedule_id, None)

#     await interaction.response.send_message(
#         f"🗑️ Schedule ID `{schedule_id}` - `{schedule['title']}` 스케줄을 삭제했습니다.",
#         ephemeral=True,
#     )

if TOKEN is None:
    raise RuntimeError("DISCORD_TOKEN이 .env에 설정되어 있지 않습니다.")

bot.run(TOKEN)
