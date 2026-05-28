import io
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_STR = os.getenv("DISCORD_GUILD_ID", "").strip()
GUILD_ID = int(GUILD_ID_STR) if GUILD_ID_STR.isdigit() else None

# 공대장 역할 설정
RAID_LEADER_ROLE_NAME = "공대장"
REQUIRE_LEADER_ROLE = False  # 실제 적용 시 True 권장

ANYTIME_VALUE = "ANYTIME"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# 메모리 저장소
schedules = {}


def get_week_dates(base_date: Optional[datetime] = None):
    """
    수요일 시작 주간 생성
    예:
    수요일 ~ 다음 주 화요일
    """
    if base_date is None:
        base_date = datetime.now()

    # Python weekday(): 월=0, 화=1, 수=2, 목=3 ...
    WEDNESDAY = 2

    days_since_wednesday = (base_date.weekday() - WEDNESDAY) % 7
    week_start = base_date - timedelta(days=days_since_wednesday)

    return [week_start + timedelta(days=i) for i in range(7)]

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
    현재 입력한 공대원 전체 기준으로 날짜별 교집합 계산
    """
    schedule = schedules[schedule_id]
    active_users = get_active_users(schedule_id)

    results = {}

    for day_key in schedule["days"].keys():
        if not active_users:
            results[day_key] = {
                "possible": False,
                "reason": "no_users",
                "start_minutes": None,
                "display_text": "참여 인원 없음",
                "missing_names": [],
            }
            continue

        missing_names = []
        candidate_minutes = []

        for _, user_data in active_users.items():
            selected = user_data["selected"]
            user_name = user_data["name"]

            if day_key not in selected:
                missing_names.append(user_name)
                continue

            candidate_minutes.append(time_value_to_minutes(selected[day_key]))

        if missing_names:
            results[day_key] = {
                "possible": False,
                "reason": "missing_users",
                "start_minutes": None,
                "display_text": "일부 공대원 미입력",
                "missing_names": missing_names,
            }
        else:
            intersection_start = max(candidate_minutes) if candidate_minutes else 0

            results[day_key] = {
                "possible": True,
                "reason": None,
                "start_minutes": intersection_start,
                "display_text": format_intersection_time(intersection_start),
                "missing_names": [],
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
    possible_days_summary = build_possible_days_summary(schedule_id)

    # 이미지 크기
    width = 2100
    header_height = 140
    title_height = 80
    cell_height = 500
    summary_box_height = 250
    footer_height = 40
    height = header_height + title_height + cell_height + 40 + summary_box_height + footer_height

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

    intersection_green_bg = (232, 247, 236)
    intersection_green_border = (82, 173, 107)
    intersection_orange_bg = (255, 244, 229)
    intersection_orange_border = (235, 162, 78)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # 폰트
    title_font = load_font(34, bold=True)
    sub_font = load_font(21, bold=False)
    sub_bold_font = load_font(21, bold=True)
    day_font = load_font(24, bold=True)
    day_sub_font = load_font(20, bold=False)
    count_font = load_font(20, bold=True)
    item_font = load_font(18, bold=False)
    small_font = load_font(17, bold=False)
    box_title_font = load_font(22, bold=True)
    box_value_font = load_font(19, bold=True)

    # 상단 타이틀
    draw.text((40, 28), f"{schedule['title']} 주간 스케줄", font=title_font, fill=title_color)
    draw.text(
        (40, 75),
        f"대상 주간: {schedule['week_start_label']} ~ {schedule['week_end_label']}   |   참여 인원: {get_participant_count(schedule_id)}명",
        font=sub_font,
        fill=sub_color
    )

    # 안내문 박스
    info_top = 110
    info_bottom = info_top + 82
    draw.rounded_rectangle((32, info_top, width - 32, info_bottom), radius=16, fill=white, outline=border, width=2)
    draw.text(
        (52, info_top + 16),
        "날짜별로 '몇 시 이후 가능' 정보와 공대원 교집합 결과가 함께 표시됩니다.",
        font=sub_font,
        fill=sub_color
    )
    draw.text(
        (52, info_top + 44),
        "입력 예시: 21 / 오후 9시 / 오후 9시 30분 / 공란=아무때나 가능",
        font=small_font,
        fill=gray_text
    )

    # 주간 달력 영역
    calendar_top = header_height + title_height - 10
    left_margin = 30
    right_margin = 30
    gap = 14
    cell_width = (width - left_margin - right_margin - gap * 6) // 7

    for idx, date_obj in enumerate(week_dates):
        x1 = left_margin + idx * (cell_width + gap)
        y1 = calendar_top
        x2 = x1 + cell_width
        y2 = y1 + cell_height

        day_key = date_obj.strftime("%Y-%m-%d")
        weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
        weekday = weekday_names[date_obj.weekday()]
        entries = summary.get(day_key, [])
        intersection = intersections[day_key]

        # 카드 배경
        draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=white, outline=border, width=2)

        # 카드 헤더
        header_h = 78
        draw.rounded_rectangle((x1, y1, x2, y1 + header_h), radius=18, fill=header_bg, outline=header_bg)
        draw.rectangle((x1, y1 + 18, x2, y1 + header_h), fill=header_bg)

        draw.text((x1 + 18, y1 + 14), date_obj.strftime("%m/%d"), font=day_font, fill=title_color)
        draw.text((x1 + 18, y1 + 44), f"{weekday}요일", font=day_sub_font, fill=sub_color)

        count_text = f"{len(entries)}명 입력" if entries else "선택 없음"
        count_fill = green if entries else gray_text

        count_bbox = draw.textbbox((0, 0), count_text, font=count_font)
        count_w = count_bbox[2] - count_bbox[0]
        draw.text((x2 - count_w - 18, y1 + 26), count_text, font=count_font, fill=count_fill)

        # 교집합 박스
        inter_box_top = y1 + header_h + 14
        inter_box_bottom = inter_box_top + 78

        if intersection["possible"]:
            inter_fill = intersection_green_bg
            inter_outline = intersection_green_border
            inter_title = "교집합 가능"
            inter_value = intersection["display_text"]
        else:
            inter_fill = intersection_orange_bg
            inter_outline = intersection_orange_border
            inter_title = "교집합 없음"

            if intersection["reason"] == "no_users":
                inter_value = "참여 인원 없음"
            elif intersection["reason"] == "missing_users":
                inter_value = f"미입력 {len(intersection['missing_names'])}명"
            else:
                inter_value = "조건 불충족"

        draw.rounded_rectangle(
            (x1 + 14, inter_box_top, x2 - 14, inter_box_bottom),
            radius=14,
            fill=inter_fill,
            outline=inter_outline,
            width=2
        )

        draw.text((x1 + 28, inter_box_top + 12), inter_title, font=box_title_font, fill=title_color)
        draw.text((x1 + 28, inter_box_top + 42), inter_value, font=box_value_font, fill=title_color)

        # 공대원 입력 리스트
        content_top = inter_box_bottom + 18
        content_left = x1 + 16
        content_right = x2 - 16

        draw.text((content_left, content_top), "공대원 입력 현황", font=sub_bold_font, fill=title_color)

        list_top = content_top + 34

        if not entries:
            draw.text((content_left, list_top + 6), "아직 입력한 공대원이 없습니다.", font=item_font, fill=gray_text)
        else:
            row_h = 34
            max_visible = 7

            for item_idx, item in enumerate(entries[:max_visible]):
                row_top = list_top + item_idx * (row_h + 8)
                row_bottom = row_top + row_h

                draw.rounded_rectangle(
                    (content_left, row_top, content_right, row_bottom),
                    radius=10,
                    fill=(245, 247, 250),
                    outline=line_color,
                    width=1
                )

                line_text = f"{format_time_korean(item['time'])} - {item['name']}"
                draw.text((content_left + 10, row_top + 7), line_text, font=item_font, fill=title_color)

            if len(entries) > max_visible:
                more_text = f"... 외 {len(entries) - max_visible}명"
                draw.text((content_left, y2 - 34), more_text, font=small_font, fill=gray_text)

    # 하단 레이드 진행 가능일 박스
    summary_top = calendar_top + cell_height + 26
    summary_bottom = summary_top + summary_box_height

    draw.rounded_rectangle(
        (32, summary_top, width - 32, summary_bottom),
        radius=18,
        fill=white,
        outline=border,
        width=2
    )

    draw.text((54, summary_top + 18), "레이드 진행 가능일", font=title_font, fill=title_color)
    draw.text(
        (54, summary_top + 64),
        "현재 시간을 입력한 공대원 전체가 공통으로 가능한 날짜와 시작 가능 시간입니다.",
        font=sub_font,
        fill=sub_color
    )

    text_y = summary_top + 108

    if "공통으로 가능한 날짜가 없습니다" in possible_days_summary or "아직 시간을 입력한 공대원이 없어" in possible_days_summary:
        draw.text((58, text_y), possible_days_summary, font=sub_bold_font, fill=gray_text)
    else:
        for line in possible_days_summary.split("\n"):
            draw.text((58, text_y), line, font=sub_bold_font, fill=title_color)
            text_y += 32

    # 하단 작은 설명
    footer_text = "※ 이미지 갱신 시 최신 참석 가능 시간이 반영됩니다."
    draw.text((36, height - 28), footer_text, font=small_font, fill=gray_text)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return discord.File(fp=buffer, filename="schedule_calendar.png")


def build_schedule_embed(schedule_id: int):
    schedule = schedules[schedule_id]
    possible_days_summary = build_possible_days_summary(schedule_id)

    embed = discord.Embed(
        title=f"📅 {schedule['title']} 스케줄",
        description=(
            "아래 버튼을 눌러 이번 주 참석 가능한 **날짜**를 고른 뒤,\n"
            "**그 날 몇 시 이후 가능한지** 입력하세요.\n\n"
            "입력 예시: `21`, `21:30`, `오전 9시`, `오후 9시`, `오후 9시 30분`\n"
            "공란 제출: `아무때나 가능`\n"
            "삭제 예시: `삭제` / `없음`\n\n"
            "※ 교집합은 **현재 시간을 입력한 공대원 전체 기준**으로 계산됩니다."
        ),
        color=discord.Color.blue(),
    )

    embed.add_field(name="생성자", value=schedule["creator_name"], inline=True)
    embed.add_field(
        name="대상 주간",
        value=f"{schedule['week_start_label']} ~ {schedule['week_end_label']}",
        inline=True,
    )
    embed.add_field(
        name="참여 인원",
        value=f"{get_participant_count(schedule_id)}명",
        inline=True,
    )

    embed.add_field(
        name="레이드 진행 가능일",
        value=possible_days_summary,
        inline=False,
    )

    embed.set_image(url="attachment://schedule_calendar.png")
    embed.set_footer(text=f"Schedule ID: {schedule_id}")

    return embed


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

        await message.edit(
            embed=embed,
            attachments=[calendar_file],
            view=ScheduleMainView(schedule_id),
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

        self.time_input = discord.ui.TextInput(
            label="몇 시 이후 가능한가요?",
            placeholder="공란=아무때나 가능 / 예: 오후 9시 / 삭제하려면 '삭제'",
            default=current_value,
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
            else:
                schedule["availability"][user_id]["selected"].pop(self.day_key, None)

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
            else:
                schedule["availability"][user_id]["selected"][self.day_key] = saved_time

            rebuild_summary(self.schedule_id)
            await update_schedule_message(self.schedule_id)

            await interaction.response.send_message(
                f"✅ `{day_label}`에 대해 **{format_time_korean(saved_time)}** 으로 저장했습니다.",
                ephemeral=True,
            )
            return

class AllDaysButton(discord.ui.Button):
    def __init__(self, schedule_id: int):
        super().__init__(
            label="전부 가능",
            style=discord.ButtonStyle.success,
            row=2,
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
    def __init__(self, schedule_id: int):
        super().__init__(
            label="내 일정 전체 삭제",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        self.schedule_id = schedule_id

    async def callback(self, interaction: discord.Interaction):
        schedule = schedules[self.schedule_id]
        user_id = str(interaction.user.id)

        if user_id in schedule["availability"]:
            schedule["availability"][user_id]["selected"] = {}
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
            row = 0 if idx < 4 else 1
            self.add_item(DayButton(schedule_id, day_key, label, row=row))

        self.add_item(AllDaysButton(schedule_id))
        self.add_item(ResetMyScheduleButton(schedule_id))

class ScheduleMainView(discord.ui.View):
    def __init__(self, schedule_id: int):
        super().__init__(timeout=None)
        self.schedule_id = schedule_id

    @discord.ui.button(
        label="주간 일정 입력하기",
        style=discord.ButtonStyle.success,
        emoji="🗓️",
        custom_id="open_week_input",
    )
    async def open_calendar(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
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


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.tree.command(
    name="스케줄생성",
    description="레이드 주간 스케줄을 생성합니다.",
)
@app_commands.describe(
    제목="예: 침식레이드, 하멘, 베히모스"
)
async def create_schedule(
    interaction: discord.Interaction,
    제목: str,
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

    schedule_id = len(schedules) + 1

    week_dates = get_week_dates()
    week_start = week_dates[0]
    week_end = week_dates[-1]

    days = {}
    for date_obj in week_dates:
        day_key = date_obj.strftime("%Y-%m-%d")
        days[day_key] = format_day_label(date_obj)

    schedules[schedule_id] = {
        "id": schedule_id,
        "title": 제목,
        "creator_id": interaction.user.id,
        "creator_name": interaction.user.display_name,
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "message_id": None,
        "week_dates": week_dates,
        "week_start_label": format_day_label(week_start),
        "week_end_label": format_day_label(week_end),
        "days": days,
        "availability": {},
        "summary": {},
    }

    rebuild_summary(schedule_id)

    embed = build_schedule_embed(schedule_id)
    calendar_file = make_calendar_file(schedule_id)
    view = ScheduleMainView(schedule_id)

    await interaction.response.send_message(
        embed=embed,
        file=calendar_file,
        view=view,
    )

    message = await interaction.original_response()
    schedules[schedule_id]["message_id"] = message.id


if TOKEN is None:
    raise RuntimeError("DISCORD_TOKEN이 .env에 설정되어 있지 않습니다.")

bot.run(TOKEN)