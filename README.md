# Telegram torrent + YouTube bot

Личный Telegram-бот: принимает magnet / `.torrent` / ссылку YouTube, качает на VPS и отправляет файлы обратно в чат через **локальный Bot API** (до ~2 ГБ на файл).

## Состав

| Сервис | Назначение |
|--------|------------|
| `telegram-bot-api` | Локальный Telegram Bot API |
| `qbittorrent` | Загрузка торрентов (WebUI только внутри Docker-сети) |
| `bot` | Python / aiogram 3 (торренты + yt-dlp) |

## Требования

- Ubuntu 24.04 VPS, root/SSH
- Docker Engine + Docker Compose plugin
- Аккаунт Telegram

## Секреты

1. **Bot token** — [@BotFather](https://t.me/BotFather) → `/newbot`
2. **API ID / API Hash** — [my.telegram.org](https://my.telegram.org) → API development tools
3. **Ваш user id** — [@userinfobot](https://t.me/userinfobot) или аналог

## Установка на VPS

```bash
# Docker (если ещё нет)
curl -fsSL https://get.docker.com | sh

# Данные и проект
mkdir -p /var/lib/torrent-bot/{downloads,bot-api,qbit-config}
# скопируйте этот репозиторий, например:
#   git clone <url> /opt/telegram-torrent-bot
cd /opt/telegram-torrent-bot   # или путь к клону

cp .env.example .env
nano .env   # заполните BOT_TOKEN, ALLOWED_USER_IDS, TELEGRAM_API_ID, TELEGRAM_API_HASH
```

### Пароль qBittorrent

Образ linuxserver/qbittorrent при первом запуске пишет временный пароль в лог:

```bash
docker compose up -d qbittorrent
docker compose logs qbittorrent | grep -i password
```

Пропишите его в `.env` как `QBITTORRENT_PASSWORD` (логин по умолчанию `admin`).  
WebUI слушает только `127.0.0.1:8080` на VPS (с интернета недоступен). Доступ с ПК:

```bash
# контейнер должен быть запущен:
docker compose up -d qbittorrent
docker compose ps   # у qbittorrent в PORTS должно быть 127.0.0.1:8080->8080

# с вашего ПК:
ssh -L 8080:127.0.0.1:8080 root@YOUR_VPS
# браузер: http://127.0.0.1:8080
```

### Запуск всего стека

```bash
# права для qBittorrent (PUID/PGID=1000 по умолчанию) — иначе состояние error при записи
mkdir -p /var/lib/torrent-bot/downloads
chown -R 1000:1000 /var/lib/torrent-bot/downloads

# в .env обязательно: TELEGRAM_LOCAL=True  (иначе лимит ~50 МБ)
docker compose up -d --build

# Убедитесь, что Bot API в local-режиме (в cmdline должен быть --local):
docker compose exec telegram-bot-api ps aux

docker compose logs -f bot
```

В Telegram: `/start` → отправьте тестовый magnet или `.torrent`.

## Поведение

- Доступ только у пользователей из `ALLOWED_USER_IDS` (через запятую)
- Одна активная задача; новая ссылка при занятости → отказ (`/cancel`)
- Торрент: выбор файлов кнопками; качаются только выбранные
- YouTube: выбор качества (лучшее / 1080 / 720 / 480 / mp3)
- Лимиты: выбранное **> 20 ГБ** или свободно **< 2 ГБ** → отказ
- Файлы **> ~1900 МБ** режутся на zip-части и шлются подряд
- После успешной отправки файлы с диска удаляются; «зависшее» — через 24 часа
- Сидирование отключено (торрент удаляется из клиента после скачивания)
- Большие файлы уходят в Telegram через `file://` (локальный Bot API), без HTTP-upload
- Команды: `/start`, `/status`, `/cancel`, `/clean`

## Переменные окружения

См. [`.env.example`](.env.example). Важные:

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Токен BotFather |
| `ALLOWED_USER_IDS` | Числовые Telegram id через запятую (`111,222`) |
| `ALLOWED_USER_ID` | Устаревший одиночный id (всё ещё поддерживается) |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Для локального Bot API |
| `QBITTORRENT_PASSWORD` | Пароль WebUI |
| `DATA_DIR` | Каталог данных на хосте (по умолчанию `/var/lib/torrent-bot`) |
| `MAX_SELECTED_BYTES` | Потолок выбранного (20 ГиБ) |
| `MIN_FREE_BYTES` | Минимум свободного места (2 ГиБ) |

## Локальная разработка (без полного стека)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# укажите реальные URL, если сервисы уже запущены
python -m bot.main
```

## Вне v1

- VPN/прокси для BitTorrent-трафика
- Webhook, публичный WebUI, очередь из нескольких задач

## Правовой дисклеймер

Используйте только для контента, который вам разрешено скачивать. Соблюдайте законы вашей страны и правила хостинг-провайдера.
