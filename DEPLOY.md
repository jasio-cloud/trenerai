# 🚀 Uruchomienie bota na Linuxie (24/7)

Bot pilnuje makro, wysyła przypomnienia na Discorda i odbiera komendy. Działa non-stop.

## Wymagania
- Python 3.10+ (`python3 --version`)
- Token bota Discord + URL webhooka (dostaniesz w pliku `.env` osobno, mailem)

## 1. Pobierz kod
```bash
git clone <ADRES_REPO> trener
cd trener
```

## 2. Zależności
```bash
python3 -m pip install -U discord.py
```

## 3. Sekrety – plik .env
Wrzuć przysłany plik `.env` do folderu `trener/` (obok `bot.py`).
Zawiera dwie linie:
```
DISCORD_BOT_TOKEN=...
DISCORD_WEBHOOK_URL=...
```
(Wzór jest w `.env.example`. Plik `.env` NIE jest w repo – to sekrety.)

W panelu Discord Developer Portal bot musi mieć włączone **MESSAGE CONTENT INTENT**
(zakładka Bot) i być zaproszony na serwer z uprawnieniami: Send Messages, Read Message History, Mention Everyone.

## 4. Test
```bash
python3 bot.py
```
Powinno wypisać: `Zalogowano jako ... – Trener AI online`. Wpisz `!pomoc` na Discordzie.

## 5. Działanie 24/7 (żeby nie gasło po zamknięciu terminala)

### Opcja A – screen (najprościej)
```bash
sudo apt install screen -y
screen -S trener
python3 bot.py
# odłącz: Ctrl+A, potem D   |   wróć: screen -r trener
```

### Opcja B – usługa systemd (sama wstaje po restarcie serwera)
Utwórz `/etc/systemd/system/trener.service`:
```ini
[Unit]
Description=Trener AI Discord bot
After=network-online.target

[Service]
WorkingDirectory=/home/UZYTKOWNIK/trener
ExecStart=/usr/bin/python3 /home/UZYTKOWNIK/trener/bot.py
Restart=always
User=UZYTKOWNIK

[Install]
WantedBy=multi-user.target
```
Potem:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trener
sudo systemctl status trener     # podgląd
journalctl -u trener -f          # logi na żywo
```

## Strefa czasowa ⏰
Godziny przypomnień liczą się wg zegara serwera. Ustaw polską strefę:
```bash
sudo timedatectl set-timezone Europe/Warsaw
```

## Aktualizacja kodu
```bash
cd trener && git pull
sudo systemctl restart trener      # albo zrestartuj screena
```
Dane (spiżarnia, waga, plan) są w `state/` i `progress/` – zostają nietknięte przy aktualizacji.
