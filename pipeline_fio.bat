@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

cd /d "C:\Users\esteb\Downloads\POSTULAPE\POSTULAPE"
if not exist logs mkdir logs

echo ==================================================== >> logs\fiorella.log
echo Inicio: %DATE% %TIME% >> logs\fiorella.log

python main.py --persona fiorella --keywords "arquitecto,supervisor de obra,asistente de arquitectura,cadista,dibujante tecnico" >> logs\fiorella.log 2>&1
set "POSTULAPE_EXIT=%ERRORLEVEL%"

echo Fin: %DATE% %TIME% - código de salida: %POSTULAPE_EXIT% >> logs\fiorella.log
exit /b %POSTULAPE_EXIT%
