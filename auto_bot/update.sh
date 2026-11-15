#!/bin/bash
# Neuen Code holen und Bot neu starten. Aufruf: bash ~/tradelab-signal-bot/auto_bot/update.sh
set -e
cd ~/tradelab-signal-bot && git pull --ff-only
python3 auto_bot/tests/test_strategie.py
sudo systemctl restart tradelab-bot
sleep 3 && sudo systemctl status tradelab-bot --no-pager | head -5
