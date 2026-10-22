# Radar Comercial Gioval

Aplicación independiente para buscar oportunidades de Compra Ágil, comparar requerimientos con un catálogo, estimar rentabilidad y mantener seguimiento comercial. Está construida con Streamlit y Python.

## Inicio rápido en Windows

1. Instala Python 3.11 o 3.12 si aún no lo tienes (activa la opción para añadir Python al PATH).
2. Ejecuta instalar_radar.bat.
3. Ejecuta iniciar_radar.bat.
4. Abre http://localhost:8502.

La primera instalación requiere internet para descargar dependencias. La aplicación se inicia localmente y no expone el puerto a otros equipos por defecto.

## Datos separados del código

Los datos locales se guardan por defecto fuera del repositorio:
- Windows: %LOCALAPPDATA%\RadarComercialGioval\Datos\
- Linux/macOS: ~/.local/share/RadarComercialGioval/Datos/

Así, actualizar el código no borra las oportunidades ni las evaluaciones. La base de datos de Radar es radar_comercial.db. La app no crea ni modifica el historial de cotizaciones de Giovalia.

## Conectar opcionalmente con la biblioteca de Giovalia

Radar puede ejecutarse sin Giovalia y sin catálogo: las búsquedas, la importación XLSX/CSV, las oportunidades manuales y el seguimiento siguen disponibles. La evaluación contra catálogo necesita un biblioteca/indice.json compatible.

Para reutilizar la biblioteca central de Giovalia, define GIOVAL_DATA_DIR o RADAR_DATA_DIR en el entorno antes de iniciar Radar y apunta a la carpeta de datos que contiene biblioteca. Para una configuración separada, deja el valor predeterminado o define RADAR_DATA_DIR con otra carpeta. RADAR_DATA_DIR tiene prioridad sobre GIOVAL_DATA_DIR.

El adaptador de integración es de solo lectura: obtiene los productos desde biblioteca/indice.json y las métricas históricas desde columnas comerciales seleccionadas de biblioteca/cotizador.db. No importa ni requiere cotizador.py ni historial.py, no modifica las cotizaciones y no trae identificadores de clientes al resumen.

## API de Compra Ágil e IA

- La búsqueda directa requiere un ticket personal de la API oficial de ChileCompra; se ingresa desde la interfaz y no se guarda.
- Si no tienes ticket, puedes importar resultados oficiales desde XLSX/CSV o ingresar una oportunidad manualmente.
- La ayuda integrada tiene guías locales. Para respuestas libres, configura OPENAI_API_KEY en un archivo local .env o introdúcela en la ventana de ayuda. El uso de API de OpenAI puede generar cargos independientes de ChatGPT.
- No subas .env, claves, datos privados ni la carpeta de datos a GitHub.

## Desarrollo

En Windows PowerShell:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    python -m streamlit run app.py --server.port 8502

Pruebas automatizadas:

    python -m unittest discover -s tests -v

## Separarlo desde Giovalia

El historial de código de la carpeta Radar puede extraerse como repositorio separado. Una vez creado el repositorio vacío en GitHub, desde un clon actualizado de Giovalia ejecuta:

    git fetch origin
    git checkout main
    git pull origin main
    git subtree split --prefix=radar_comercial -b radar-comercial-root
    git remote add radar-origin https://github.com/Harimcito/Radar-Comercial-Gioval.git
    git push radar-origin radar-comercial-root:main

Si Git indica que radar-origin ya existe, no lo agregues otra vez; revisa git remote -v. Este proceso publica solo el contenido de radar_comercial/ en el nuevo repositorio y no elimina Radar de Giovalia. Una vez publicado, podrás desarrollar cada aplicación por separado.

## Límites

Radar es una ayuda de análisis, no presenta ofertas en Mercado Público ni garantiza adjudicaciones. Verifica las condiciones oficiales, equivalencias, precios, IVA y logística antes de cotizar.