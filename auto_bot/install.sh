#!/bin/bash
# Einmalige Einrichtung auf dem Server (Ubuntu). Aufruf: bash ~/tradelab-signal-bot/auto_bot/install.sh
set -e
sudo apt-get update -y
sudo apt-get install -y python3 git unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades
sudo timedatectl set-timezone UTC
mkdir -p ~/tradelab-daten
if [ ! -f ~/.tradelab.env ]; then
  cp ~/tradelab-signal-bot/auto_bot/env.beispiel ~/.tradelab.env
fi
chmod 600 ~/.tradelab.env
sudo cp ~/tradelab-signal-bot/auto_bot/tradelab-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tradelab-bot
echo "Fertig. Jetzt Schlüssel eintragen:  nano ~/.tradelab.env"
echo "Danach starten:                     sudo systemctl start tradelab-bot"
echo "Logs anschauen:                     journalctl -u tradelab-bot -f"
echo "Server-IP (für OKX-API-Schlüssel):  curl -s ifconfig.me"
