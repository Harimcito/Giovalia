# Cliente de escritorio de Gioval

El instalador de Windows abre una ventana propia con pywebview/WebView2, pero el contenido y los datos comerciales están alojados en un único servicio central. El cliente no inicia una base de datos local ni guarda copias autónomas del catálogo.

Lee SHARED_LIBRARY.md para configurar Render, proteger el acceso, importar la biblioteca existente y distribuir instaladores.

## Cliente Windows

El flujo GitHub Actions "Crear instalador de escritorio para Windows" compila el cliente y publica el artefacto Gioval-Instalador-Windows-x64. Después de completar un despliegue en Render y confirmar la URL del servicio:

1. Descarga el artefacto más reciente de GitHub Actions.
2. Extrae Gioval-Setup.exe del ZIP.
3. Ejecuta el instalador en cada computador.
4. Comparte la clave de acceso de la biblioteca solo con las personas autorizadas.

El cliente requiere conexión a internet y Microsoft Edge WebView2 Runtime. No requiere que la persona instale Python ni ejecute comandos.

No distribuyas el instalador antes de que el servicio central esté desplegado y la URL configurada. Si la dirección cambia, actualiza DEFAULT_CLOUD_URL en desktop_launcher.py y genera el instalador de nuevo.
