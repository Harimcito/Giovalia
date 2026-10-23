# Cliente de escritorio de Gioval

El instalador de Windows abre una ventana propia con pywebview/WebView2 y se conecta a un servidor central. El cliente no guarda una base de datos comercial local.

## Alternativa gratuita recomendada

Consulta [GIOVAL_GRATIS.md](GIOVAL_GRATIS.md) para ejecutar el servidor en un computador Windows existente y compartir la biblioteca sin pagar alojamiento mensual.

- En el computador central descarga el repositorio y ejecuta `instalar_servidor_gioval.bat` una vez.
- Después inicia el servidor con `iniciar_servidor_gioval.bat` y deja la ventana abierta.
- En cada computador cliente instala `Gioval-Setup.exe`.
- En el menú Inicio, ejecuta **Configurar conexión de Gioval** y escribe la dirección central que corresponda: HTTP privado dentro de la oficina o HTTPS si se configuró un túnel seguro para acceder desde internet.
- Comparte la clave de acceso solo con quienes estén autorizados.

La modalidad de red local no tiene costo de alojamiento, pero el computador servidor debe permanecer encendido. El cliente requiere Windows x64, conexión a la red del servidor y Microsoft Edge WebView2 Runtime. No requiere Python en los equipos clientes.

La ventana de escritorio permite descargas del navegador integrado. Así los botones de PDF del cotizador y del historial pueden guardar el archivo en el computador cliente; en una pestaña normal del navegador, Streamlit usa la descarga habitual.

## Actualizar equipos ya instalados

Puedes ejecutar el nuevo `Gioval-Setup.exe` sobre la versión existente: mantiene el mismo identificador de instalación y la misma carpeta de preferencias, por lo que conserva la dirección del servidor configurada y los datos que viven fuera del programa. No desinstales Gioval ni borres `%LOCALAPPDATA%\Gioval\Datos`. El instalador actualiza el icono y crea o renueva el acceso directo **Gioval** en el escritorio.

La barra y las pantallas de bienvenida se entregan desde el servidor. En un servidor Windows actualiza el código de Gioval y reinicia sus servicios; en Render, publica el despliegue correspondiente. Instalar el cliente por sí solo no cambia la interfaz que entrega un servidor sin actualizar.

## Perfiles y privacidad

Gioval muestra un selector de perfiles por sesión para personalizar el saludo. Los perfiles se guardan solo en la sesión actual del navegador y no son cuentas ni verifican la identidad de quien usa el equipo. Al cambiar de perfil se limpian los datos transitorios de esa sesión; el borrador actual se guarda primero mediante el flujo normal de la aplicación. El catálogo, el historial y los datos de Radar siguen perteneciendo al espacio compartido y no se separan por perfil. En modo compartido se mantienen las reglas existentes que excluyen datos identificatorios de clientes del historial común.

## Compilar el cliente Windows manualmente

En Windows x64, con Python instalado para desarrollo:

    python -m pip install -r requirements-desktop.txt
    python -m PyInstaller --noconfirm --clean gioval_desktop.spec
