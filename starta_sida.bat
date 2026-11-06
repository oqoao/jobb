@echo off
rem Dubbelklicka för att starta jobbsidan. Stäng fönstret för att avsluta.
cd /d "%~dp0"
python sida.py
if errorlevel 1 pause
