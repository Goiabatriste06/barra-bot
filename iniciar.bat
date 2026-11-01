@echo off
title Barra Bot
cd /d "%~dp0"
echo Instalando dependencias...
py -m pip install -r requirements.txt
echo.
echo Abrindo o app...
py barra_bot.py
pause
