@echo on
chcp 65001 >nul
rem update_daily.bat - full Dogsout update pipeline
rem Triggered by telegram/bot.py's /reload command (runs this bat directly).
rem All actual commands run inside WSL (conda env "dogsout" activated first).
rem See update_daily.sh for the real pipeline logic.
wsl bash -ic "conda activate dogsout && bash /mnt/d/dogsout/data/data_analysis/update_daily.sh"
exit /b %errorlevel%
