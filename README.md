# Raid Schedule Discord Bot

디스코드에서 레이드 공대원의 주간 가능 시간을 입력받고, 가능한 날짜와 시간을 자동으로 정리해주는 레이드 스케줄 관리 봇입니다.

공대장이 스케줄을 생성하면 공대원들이 날짜별로 가능한 시간을 입력할 수 있고, 봇은 입력값을 기반으로 주간 달력 이미지를 생성하여 디스코드 메시지에 표시합니다.

---

## 현재 버전

```text
v1.20
```

---

## 주요 기능

### 스케줄 생성

공대장이 `/스케줄생성` 명령어로 레이드 스케줄을 생성할 수 있습니다.

```text
/스케줄생성 제목: 침식레이드
```

스케줄은 매주 수요일을 시작일로 하여 다음 주 화요일까지의 범위로 생성됩니다.

예:

```text
05/28 (수) ~ 06/03 (화)
```

---

### 공대원 가능 시간 입력

공대원은 스케줄 메시지의 `주간 일정 입력하기` 버튼을 눌러 가능한 날짜와 시간을 입력할 수 있습니다.

지원하는 입력 예시는 다음과 같습니다.

```text
21
21:30
오전 9시
오후 9시
오후 9시 30분
```

입력값은 자동으로 다음과 같은 형식으로 변환되어 표시됩니다.

```text
오후 09시 30분 이후
```

시간 입력창을 공란으로 제출하면 다음과 같이 처리됩니다.

```text
아무때나 가능
```

---

### 전부 가능 입력

날짜 선택 화면에서 `전부 가능` 버튼을 사용할 수 있습니다.

`전부 가능`을 선택한 뒤 시간을 입력하면 해당 주간의 모든 날짜에 같은 가능 시간이 적용됩니다.

예:

```text
전부 가능
→ 오후 9시 입력
→ 모든 날짜에 오후 09시 00분 이후 가능으로 저장
```

공란으로 제출하면 모든 날짜가 `아무때나 가능`으로 저장됩니다.

---

### 주간 달력 이미지 생성

공대원들의 입력 현황은 텍스트 표가 아니라 PNG 이미지 형태의 주간 달력으로 표시됩니다.

달력에는 날짜별로 다음 정보가 표시됩니다.

```text
날짜
요일
입력 인원 수
진행 가능 시간
공대원 입력 현황
```

한 날짜에 최대 8명의 공대원 입력 현황이 표시되도록 구성되어 있습니다.

---

### 레이드 진행 가능일 계산

봇은 각 날짜별 입력값을 기준으로 레이드 진행 가능 여부를 계산합니다.

현재 기준은 다음과 같습니다.

```text
6명 이상 가능해야 레이드 진행 가능일로 표시
```

진행 가능 시간은 해당 날짜에 가능한 공대원들의 가능 시작 시간 중 가장 늦은 시간으로 계산됩니다.

예:

```text
공대원 A: 오후 8시 이후
공대원 B: 오후 9시 이후
공대원 C: 아무때나 가능

진행 가능 시간: 오후 09시 00분 이후
```

---

### 스케줄 목록 확인

`/스케줄목록` 명령어로 현재 활성화된 스케줄 목록을 확인할 수 있습니다.

```text
/스케줄목록
```

스케줄 목록에서는 다음 정보가 표시됩니다.

```text
Schedule ID
스케줄 제목
대상 주간
참여 인원
레이드 진행 가능일 또는 레이드 진행 예정일
스케줄 상태
```

---

### 스케줄 삭제

스케줄 삭제는 `/스케줄목록`에서 버튼을 눌러 수행합니다.

```text
/스케줄목록
→ 삭제할 스케줄의 삭제 버튼 클릭
```

삭제된 스케줄은 활성 목록에서 제외되며, 기존 디스코드 스케줄 메시지는 삭제된 스케줄로 표시되고 더 이상 입력을 받을 수 없습니다.

`/스케줄삭제` 명령어 방식은 제거되었습니다.

---

### 스케줄 확정

`/스케줄목록`에서 스케줄별 `확정` 버튼을 눌러 레이드 진행 예정일을 확정할 수 있습니다.

```text
/스케줄목록
→ 확정할 스케줄의 확정 버튼 클릭
```

확정 방식은 두 가지입니다.

```text
1. 레이드 진행 가능일 중 선택
2. 직접 입력으로 확정
```

레이드 진행 가능일 선택 방식에서는 현재 계산된 레이드 진행 가능일이 선택지로 표시됩니다.

여러 개의 레이드 진행 가능일을 동시에 선택하여 확정할 수 있습니다.

예:

```text
05/29 (금) - 오후 09시 00분 이후
05/30 (토) - 오후 08시 30분 이후
06/01 (월) - 아무때나 가능
```

직접 입력 방식에서는 레이드 진행 가능일 외의 날짜나 시간을 직접 입력하여 확정할 수 있습니다.

예:

```text
06/01 (월) 오후 9시
06/03 (수) 오후 10시
```

여러 줄로 입력하면 여러 개의 레이드 진행 예정일로 저장됩니다.

---

### 확정된 스케줄 처리

스케줄이 확정되면 기존 스케줄 메시지에서 `주간 일정 입력하기` 버튼이 제거됩니다.

확정된 스케줄은 더 이상 공대원 가능 시간 입력을 받지 않습니다.

확정 전 표시:

```text
레이드 진행 가능일
```

확정 후 표시:

```text
레이드 진행 예정일
```

확정 후에는 주간 달력 이미지와 `/스케줄목록`에서도 `레이드 진행 예정일` 기준으로 표시됩니다.

---

### 여러 개의 레이드 진행 예정일 저장

v1.20부터 확정 일정은 별도 테이블로 관리됩니다.

```text
confirmed_schedules
```

기존에는 스케줄 하나당 확정 일정 1개만 저장하는 구조였지만, v1.20부터는 스케줄 하나에 여러 개의 확정 일정을 저장할 수 있습니다.

예:

```text
침식레이드
├── 05/29 (금) - 오후 09시 00분 이후
├── 05/30 (토) - 오후 08시 30분 이후
└── 06/01 (월) - 아무때나 가능
```

---

### SQLite 저장

스케줄과 공대원 입력 정보는 SQLite DB에 저장됩니다.

```text
raid_schedule.db
```

따라서 봇을 재시작해도 기존 스케줄과 입력값이 유지됩니다.

저장되는 주요 정보는 다음과 같습니다.

```text
스케줄 제목
생성자
서버 ID
채널 ID
메시지 ID
주간 시작일
주간 종료일
공대원 Discord ID
공대원 닉네임
날짜별 가능 시간
확정된 레이드 진행 예정일
```

---

### 오래된 스케줄 자동 정리

DB에 저장된 스케줄이 10개를 초과하면, 가장 오래된 스케줄부터 자동으로 완전 삭제됩니다.

```text
최대 저장 스케줄 수: 10개
```

삭제 대상은 다음과 같습니다.

```text
schedules 테이블의 오래된 스케줄
availability 테이블의 해당 스케줄 입력값
confirmed_schedules 테이블의 해당 스케줄 확정값
```

단, SQLite의 AUTOINCREMENT 특성상 Schedule ID 숫자는 삭제 후에도 계속 증가할 수 있습니다.

예:

```text
기존 ID: 1 ~ 10
새 스케줄 생성: 11
DB 정리 후 남는 스케줄: 2 ~ 11
```

---

## 파일 구조

```text
raid-schedule-discord-bot/
├── bot.py
├── db.py
├── requirements.txt
├── README.md
├── .gitignore
├── .env              # GitHub 업로드 제외
├── raid_schedule.db  # GitHub 업로드 제외
└── venv/             # GitHub 업로드 제외
```

---

## 설치 방법

### 1. 저장소 클론

```bash
git clone https://github.com/hosama-92/raid-schedule-discord-bot.git
cd raid-schedule-discord-bot
```

---

### 2. 시스템 패키지 설치

Ubuntu / WSL 기준:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip fonts-nanum sqlite3
```

`fonts-nanum`은 달력 이미지에서 한글이 깨지지 않도록 하기 위해 사용합니다.

---

### 3. Python 가상환경 생성

```bash
python3 -m venv venv
source venv/bin/activate
```

---

### 4. Python 패키지 설치

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

### 5. `.env` 파일 생성

```bash
vi .env
```

내용:

```env
DISCORD_TOKEN=디스코드_봇_토큰
DISCORD_GUILD_ID=디스코드_서버_ID
```

예:

```env
DISCORD_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxx
DISCORD_GUILD_ID=1509528603409383514
```

`.env` 파일은 봇 토큰이 포함되므로 GitHub에 업로드하면 안 됩니다.

---

### 6. 봇 실행

```bash
python bot.py
```

정상 실행 예:

```text
Logged in as 초열공대 레이드 스케줄 봇#2643
Loaded 0 active schedule(s) from SQLite
Synced 2 command(s) to guild 1509528603409383514
```

---

## AWS Lightsail 24시간 운영

AWS Lightsail Ubuntu 서버에서 systemd 서비스로 봇을 24시간 실행할 수 있습니다.

### 서버 패키지 설치

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip fonts-nanum sqlite3
```

---

### 코드 클론

```bash
cd ~
git clone https://github.com/hosama-92/raid-schedule-discord-bot.git
cd raid-schedule-discord-bot
```

---

### 가상환경 구성

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

### `.env` 작성

```bash
nano .env
```

```env
DISCORD_TOKEN=디스코드_봇_토큰
DISCORD_GUILD_ID=디스코드_서버_ID
```

---

### 수동 실행 테스트

```bash
python bot.py
```

정상 동작 확인 후 `Ctrl + C`로 종료합니다.

---

### systemd 서비스 등록

```bash
sudo nano /etc/systemd/system/raidbot.service
```

내용:

```ini
[Unit]
Description=Discord Raid Schedule Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/raid-schedule-discord-bot
ExecStart=/home/ubuntu/raid-schedule-discord-bot/venv/bin/python /home/ubuntu/raid-schedule-discord-bot/bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

서비스 적용:

```bash
sudo systemctl daemon-reload
sudo systemctl enable raidbot
sudo systemctl start raidbot
```

상태 확인:

```bash
sudo systemctl status raidbot
```

로그 확인:

```bash
journalctl -u raidbot -f
```

---

## 운영 명령어

### 봇 상태 확인

```bash
sudo systemctl status raidbot
```

### 봇 재시작

```bash
sudo systemctl restart raidbot
```

### 봇 중지

```bash
sudo systemctl stop raidbot
```

### 봇 로그 확인

```bash
journalctl -u raidbot -f
```

---

## 코드 업데이트 방법

로컬에서 수정 후 GitHub에 push한 뒤, AWS Lightsail 서버에서 다음 명령어를 실행합니다.

```bash
cd ~/raid-schedule-discord-bot
git pull

source venv/bin/activate
python -m pip install -r requirements.txt

sudo systemctl restart raidbot
sudo systemctl status raidbot
```

---

## requirements.txt

```txt
discord.py
python-dotenv
Pillow
```

---

## GitHub 업로드 제외 파일

다음 파일은 `.gitignore`에 포함되어야 합니다.

```gitignore
.env
venv/
__pycache__/
*.pyc
*.db
*.sqlite3
```

---

## 버전 기록

### v1.00

초기 GitHub 업로드 버전입니다.

주요 기능:

```text
디스코드 봇 기본 실행
/스케줄생성 명령어
공대원 가능 시간 입력
주간 달력 이미지 생성
SQLite 기반 스케줄/입력값 저장
레이드 진행 가능일 계산
```

---

### v1.10

스케줄 관리 기능 개선 버전입니다.

변경 사항:

```text
/스케줄목록에서 삭제 버튼으로 스케줄 삭제 가능
/스케줄삭제 명령어 제거
DB에 저장된 스케줄이 10개를 초과하면 오래된 스케줄 자동 삭제
스케줄 삭제 로직 공통화
스케줄 목록 중심의 관리 방식으로 변경
README.md 추가
```

관리 정책:

```text
활성 스케줄은 /스케줄목록에서 확인
스케줄 삭제는 목록의 삭제 버튼으로 수행
DB에는 최대 10개의 스케줄만 유지
.env와 raid_schedule.db는 GitHub 업로드 제외
```

---

### v1.20

스케줄 확정 기능 추가 버전입니다.

변경 사항:

```text
/스케줄목록에서 스케줄 확정 버튼 추가
레이드 진행 가능일 중 선택하여 스케줄 확정 가능
직접 입력으로 스케줄 확정 가능
확정된 스케줄은 주간 일정 입력 버튼 제거
확정 후 레이드 진행 가능일 문구를 레이드 진행 예정일로 변경
여러 개의 레이드 진행 예정일 확정 지원
confirmed_schedules 테이블 추가
확정 일정 정보를 스케줄과 분리하여 저장
AWS Lightsail 24시간 운영 절차 README에 추가
```

확정 정책:

```text
확정 전: 레이드 진행 가능일 표시
확정 후: 레이드 진행 예정일 표시
확정된 스케줄은 더 이상 공대원 입력을 받지 않음
스케줄 하나에 여러 개의 레이드 진행 예정일 저장 가능
```

DB 변경 사항:

```text
confirmed_schedules 테이블 추가
스케줄별 확정 일정 다중 저장
오래된 스케줄 삭제 시 confirmed_schedules 데이터도 함께 삭제
```

---

## 향후 개선 예정

```text
스케줄 확정 취소 기능
확정된 스케줄 재수정 기능
공대원 고정 명단 관리
미입력 공대원 표시
레이드 진행 가능일 추천 강화
관리자 권한 세분화
AWS Lightsail 배포 자동화
```
