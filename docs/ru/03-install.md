# Установка

Разворачивание с нуля на хосте с NVIDIA GPU и Ubuntu.

> Инструкция описывает текущую конфигурацию (x86-хост с GPU). Вариант на
> Raspberry Pi 5 + AI HAT+ будет добавлен после переезда — см.
> [02-hardware.md](02-hardware.md).

## Предварительные требования

- Ubuntu (проверено на 26.04)
- NVIDIA GPU + драйверы + `nvidia-container-toolkit`
- Docker и `docker compose`
- Камеры с RTSP, **со статическими IP** (см. ниже)
- VirtualBox — под VM с Home Assistant

Проверка, что Docker видит GPU:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

---

## 1. Сеть и камеры

**Закрепи IP камер.** По DHCP адрес рано или поздно уедет, и конфиг
Frigate сломается. Резервирование по MAC на роутере или статика на самой
камере.

Проверь RTSP до всякого Frigate:

```bash
ffprobe -rtsp_transport tcp "rtsp://USER:PASSWORD@CAMERA_IP:554/h264/ch1/main/av_stream"
```

Если здесь не работает — Frigate не поможет.

У EZVIZ пароль RTSP — это код верификации **с наклейки на корпусе**, а не
пароль от аккаунта в приложении. И проверь, что `Local RTSP` включён:
он самопроизвольно отключается.

---

## 2. Frigate

```bash
git clone https://github.com/nisonengineer-lab/engineer-against-dementia.git
cd engineer-against-dementia/frigate

cp .env.example .env                   # логины камер и MQTT
cp config.example.yaml config.yaml
```

Заполни `.env`. Пароли в `config.yaml` **не пишутся** — Frigate
подставляет переменные `{FRIGATE_*}`.

Собери детектор:

```bash
../models/yolov9/build.sh s 320
```

Запуск:

```bash
docker compose up -d
docker compose logs -f frigate
```

Интерфейс: `http://HOST_IP:5000`

### Зоны и маски

Координаты в `config.example.yaml` — от конкретной установки. Переносить
их бессмысленно. Рисуй свои: **Settings → Mask & Zone Editor**.

Порядок такой:

1. **Сначала маска.** Всё, что не твоя территория — улица, соседский
   двор, крона дерева. Без маски прохожие генерируют тревоги круглые сутки.
2. **Потом зоны.** Разделяй по смыслу: «двор» (вышел, но на территории)
   и «калитка» (тревога) — разные зоны с разной логикой.
3. `inertia: 1` для зоны тревоги — человек проходит калитку за секунды.
   `inertia: 3` для бытовых зон, чтобы не дёргаться на проход мимо.

Подробнее: [04-configuration.md](04-configuration.md).

---

## 3. Home Assistant в VM

Home Assistant OS ставится образом в VirtualBox. Внутри VM — add-on
**Mosquitto broker**, он же MQTT-брокер для Frigate.

После установки:

- закрепи IP виртуалки статически;
- создай пользователя MQTT, его логин/пароль пойдут в `frigate/.env`;
- установи интеграцию **Frigate** (через HACS) — она подхватит камеры и
  события автоматически;
- пропиши `trusted_proxies` (см. шаг 5).

Автозапуск VM без графической сессии — `ops/systemd/`.

---

## 4. Уведомления

Telegram-бот и автоматизации Home Assistant — отдельный документ:
[05-alerts.md](05-alerts.md).

---

## 5. Доступ снаружи (Cloudflare Tunnel)

Порты на роутере **не пробрасываются**. Туннель устанавливает исходящее
соединение изнутри дома.

Конфиг: `ops/cloudflared/config.example.yml`, поддомены на каждый сервис.

**Обязательно** в `configuration.yaml` Home Assistant:

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 10.0.0.X        # IP хоста, с которого приходит туннель
```

Без этого HA видит все запросы как приходящие от прокси и ломает
аутентификацию.

---

## 6. Устойчивость

Ставится один раз и экономит много ночей.

### Health-check в cron

`ops/scripts/check-ha-health.sh` — каждые 5 минут проверяет доступность
HA и Frigate, поднимает упавшее.

```bash
crontab -e
# */5 * * * * /usr/local/bin/check-ha-health.sh >> /var/log/ha-health.log 2>&1
```

**От обычного пользователя, не от root** — VM VirtualBox принадлежит
пользователю, из-под root `VBoxManage` её не увидит.

### Запрет сна

Ноутбук-хост с закрытой крышкой не должен засыпать:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

### Отключение EEE на сетевой карте

Energy-Efficient Ethernet вызывал периодические обрывы сети:

```bash
sudo ethtool --set-eee eno2 eee off
```

Чтобы переживало перезагрузку — systemd-юнит в `ops/systemd/`.

---

## Проверка

| Что проверить | Как |
|---|---|
| Frigate видит камеры | UI на :5000, живая картинка с обеих |
| Детектор работает | пройди перед камерой, счётчик детекций растёт |
| MQTT связан | в HA появились сущности `binary_sensor.CAMERA_person` |
| Зона срабатывает | зайди в зону тревоги, событие в `frigate/events` |
| Уведомление доходит | Telegram + push на телефон |
| Health-check живой | останови контейнер, через 5 минут он должен подняться |
| Туннель работает | открой домен с мобильного интернета, не из домашней сети |

Что делать, если что-то из этого не сработало —
[07-troubleshooting.md](07-troubleshooting.md).
