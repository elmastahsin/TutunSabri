# Tütün Sabri

An async Telegram bot that monitors train availability and holds a seat the moment one opens up.

## How it works

1. A user submits a departure station, arrival station, date, and departure hour.
2. The bot creates a background task that polls for availability on a configurable interval.
3. When a seat becomes available the bot immediately attempts to hold it.
4. The user is notified and the seat is kept held for a configurable window (default 10 minutes) so they can complete the purchase manually.
5. The user can cancel a search or release a held seat at any time.

## Stack

| Layer | Technology |
|---|---|
| Bot framework | [aiogram](https://docs.aiogram.dev/) 3.x |
| Database | SQLite via SQLAlchemy (async) |
| Task queue | [TaskIQ](https://taskiq-python.github.io/) + Redis |
| HTTP client | [curl-cffi](https://github.com/yifeikong/curl-cffi) |
| Config | Pydantic Settings |

## Requirements

- Python 3.9+
- Redis

## Setup

```bash
# 1. Clone and install dependencies
uv sync

# 2. Copy and fill in the environment file
cp .env.example .env

# 3. Start Redis (if not already running)
redis-server

# 4. Run the bot
tutunsabri
```

## Configuration

All configuration is done through environment variables (or a `.env` file).

## Ollama entegrasyonu

Bot, `/ai` komutuyla aynı sunucudaki Ollama servisine bağlanır. Entegrasyon
yalnızca `http://127.0.0.1:11434` adresini ve hızlı yanıt veren
`qwen3:4b-instruct` modelini kullanır.

Linux sunucuda Ollama'yı kurun:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Ardından modeli indirin:

```bash
ollama pull qwen3:4b-instruct
```

Servisin çalıştığını kontrol edin:

```bash
systemctl status ollama
```

Ardından Telegram'da bota bir soru gönderin:

```text
/ai Merhaba
```

## Günlük haber özeti

Bot her gün Türkiye saatiyle 09.00 ve 19.00'da Google Haberler RSS üzerinden gündem,
teknoloji ve yazılım başlıklarını toplar. Yerel Ollama bu başlıkları Türkçe ve
kısa bir sabah bültenine dönüştürür; bülten aktif admin kullanıcılara gönderilir.
Ollama geçici olarak kullanılamazsa bağlantıları içeren ham başlık listesi
gönderilir. İnternet bağlantısı ve çalışan Ollama servisi önerilir.
Kayıtlı kullanıcılar `/news` komutuyla istedikleri zaman kendileri için güncel
bir bülten oluşturabilir.

`/podcast` komutu aynı haberlerden Ollama ile kısa bir Türkçe anlatım metni
hazırlar, metni Edge TTS'in Türkçe neural sesiyle MP3 olarak seslendirir ve
isteği yapan kullanıcıya gönderir. Varsayılan ses `tr-TR-AhmetNeural`, konuşma
hızı ise `%5` yavaştır. Bunlar `PODCAST_TTS_VOICE` ve `PODCAST_TTS_RATE`
değişkenleriyle ayarlanabilir. Ses üretimi internet bağlantısı gerektirir.

## Access model

- New users have no access by default. They can send an access request through the bot, which notifies all admins.
- An admin approves or rejects requests via `/grant` and `/revoke`.
- Roles determine how many searches can run in parallel: **basic** (3), **premium** (5), **admin** (unlimited).

## Database migrations

The bot applies lightweight SQLite migrations automatically on startup, so there is no separate migration step when upgrading.

---

## Legal disclaimer

This project is developed strictly for **educational purposes**.

- It is not designed to cause harm, disrupt service availability, perform denial-of-service attacks, or interfere with any third-party system or infrastructure.
- Automated interaction with third-party services may violate their terms of service. **You are solely responsible** for ensuring your use complies with all applicable laws and the terms of service of any platform you interact with.

**Use at your own risk.** The author(s) accept no liability for any damages, account bans, legal consequences, or any other outcome arising from the use or misuse of this software.
