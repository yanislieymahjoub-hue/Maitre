@echo off
REM Wrapper qui lance le script autonome d'envoi du compte rendu journalier.
REM Active le PC au besoin et envoie le rapport par mail.
cd /d "%~dp0"
python "%~dp0daily_report_standalone.py" %*
