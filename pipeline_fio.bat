@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

cd /d "C:\Users\esteb\Downloads\POSTULAPE\POSTULAPE"
if not exist logs mkdir logs

echo ==================================================== >> logs\fiorella.log
echo Inicio: %DATE% %TIME% >> logs\fiorella.log

python main.py --persona fiorella --keywords "arquitecto,supervision de obras" --paginas 1 >> logs\fiorella.log 2>&1

echo Fin: %DATE% %TIME% >> logs\fiorella.log