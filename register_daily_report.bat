@echo off
REM Enregistre une tache Windows "AssistantDailyReport" qui execute
REM daily_report.bat chaque jour a l'heure indiquee.
REM
REM Options :
REM   /ST HH:MM        heure de declenchement quotidien (24h)
REM   /RL HIGHEST      privileges maximum
REM   /F               remplace la tache existante
REM   /SC DAILY        periodicite quotidienne
REM
REM Utilisation : clic droit > Executer en tant qu'administrateur
REM
REM Pour MODIFIER l'heure : relancer ce script ou utiliser
REM l'option "Heure du compte rendu" dans l'application, puis relancer.
REM
REM Pour que le PC se reveille automatiquement a l'heure choisie :
REM   1) Ouvrir le Planificateur de taches (taskschd.msc)
REM   2) Trouver "AssistantDailyReport"
REM   3) Proprietes > Conditions > cocher "Reveiller l'ordinateur pour
REM      executer cette tache"
REM
setlocal
set TASKNAME=AssistantDailyReport
set SCRIPT=%~dp0daily_report.bat

REM Heure par defaut : 18:00. Modifiable en passant HH:MM en argument.
set HOUR=%~1
if "%HOUR%"=="" set HOUR=18:00

echo Enregistrement de la tache "%TASKNAME%" a %HOUR%...
schtasks /Create /SC DAILY /TN "%TASKNAME%" /TR "\"%SCRIPT%\"" /ST %HOUR% /RL HIGHEST /F

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ✓ Tache creee. Le compte rendu sera envoye chaque jour a %HOUR%.
    echo   Pour l'executer maintenant : schtasks /Run /TN "%TASKNAME%"
    echo   Pour la supprimer :         schtasks /Delete /TN "%TASKNAME%" /F
) else (
    echo.
    echo ✗ Echec de l'enregistrement. Relancez en tant qu'administrateur.
)
pause
endlocal
