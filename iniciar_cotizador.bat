@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Preparando la app por primera vez, puede tardar unos minutos...
  py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Verificando componentes...
pip install -q -r requirements.txt
streamlit run app.py
pause
