@echo off
rem Startet einen lokalen Server fuer das Dashboard und oeffnet es im Browser.
rem Doppelklick genuegt. Beenden: dieses Fenster schliessen.
cd /d "%~dp0docs"
echo Starte lokalen Server auf http://localhost:8899 ...
start "HL-Dashboard-Server" /min cmd /c "python -m http.server 8899"
timeout /t 2 >nul
start "" "http://localhost:8899"
echo.
echo Dashboard geoeffnet. Zum Beenden dieses Fenster und das Server-Fenster schliessen.
pause
