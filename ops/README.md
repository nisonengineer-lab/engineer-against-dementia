# ops — то, что держит систему живой

Домашняя система жизнеобеспечения без дежурного администратора. Всё в
этой папке существует потому, что что-то сломалось в реальности, а не для
полноты.

| Файл | Что чинит |
|---|---|
| `scripts/check-ha-health.sh` | Упавшие HA / Frigate. Cron каждые 5 минут |
| `systemd/homeassistant-vm.service` | Автозапуск VM с HA без графической сессии |
| `systemd/nic-eee-off.service` | Периодические обрывы сети из-за Energy-Efficient Ethernet |
| `cloudflared/config.example.yml` | Доступ снаружи без проброса портов |

## Две ошибки, которые стоили времени

**1. Cron от root.**
`check-ha-health.sh` должен стоять в crontab **обычного пользователя**.
VM VirtualBox принадлежит пользователю; из-под root `VBoxManage` её не
видит и скрипт молча ничего не делает.

**2. Запрет сна.**
Ноутбук-хост с закрытой крышкой уходит в сон и уносит с собой всю
систему:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

## Чего здесь нет

Хост изредка **полностью выключается** — не спит, а power off. Известный
задокументированный баг, связка ASUS BIOS/EC + ядро Linux. Программно не
лечится. Обходится переездом на Raspberry Pi 5.
См. [docs/ru/07-troubleshooting.md](../docs/ru/07-troubleshooting.md).
