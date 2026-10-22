# Giovalia: biblioteca compartida en la nube

## Modelo de funcionamiento

Las aplicaciones de escritorio son ventanas cliente. Todas abren el mismo servicio central de Giovalia; la base SQLite, catálogo, fotos, fichas, clientes, cotizaciones, archivos Excel/PDF y numeración residen bajo el disco persistente del servidor. El cliente de escritorio no guarda cotizaciones en una biblioteca local ni recurre silenciosamente a modo offline.

El servicio de este Blueprint usa Render con disco persistente. Render indica que el filesystem normal del servicio es efímero y que el disco persistente requiere un web service pagado; el disco es accesible por una sola instancia, por lo que esta configuración mantiene una instancia para proteger la base SQLite. Referencias:
- https://render.com/docs/disks
- https://render.com/docs/blueprint-spec

## Publicar la biblioteca central

1. Sube la rama integrada a GitHub (ya estará en main cuando el PR de biblioteca compartida se fusione).
2. Crea/inicia sesión en Render y elige New → Blueprint para conectar Harimcito/Giovalia.
3. Render detectará render.yaml. Confirma la creación del servicio y su disco persistente; requiere el plan starter por el disco.
4. Cuando solicite GIOVAL_ACCESS_PASSWORD, define una clave larga y exclusiva y compártela solo con quienes deban usar la biblioteca. No se incluye en el instalador ni en GitHub.
5. Espera a que el servicio gioval-cotizador-compartido quede publicado en https://gioval-cotizador-compartido.onrender.com.
6. Descarga el nuevo artefacto del flujo Crear instalador de escritorio para Windows y distribuye Gioval-Setup.exe.

Si Render asigna una URL distinta, actualiza DEFAULT_CLOUD_URL en desktop_launcher.py y vuelve a generar el instalador. No distribuyas el cliente hasta comprobar que la URL real corresponde al servicio.

## Acceso y datos

GIOVAL_REQUIRE_ACCESS=true protege la aplicación con la clave GIOVAL_ACCESS_PASSWORD. Quien tenga esa clave puede consultar y editar la biblioteca compartida completa, incluidos los datos de clientes y cotizaciones. Es una clave compartida, no identifica a cada persona; si más adelante se necesita revocar usuarios individualmente y tener auditoría por persona, habrá que implementar cuentas individuales.

La clave de OpenAI es opcional. Si se quieren usar funciones IA, el administrador puede añadir OPENAI_API_KEY como variable secreta del servicio en Render. En modo compartido la interfaz no permite que cada sesión sobrescriba la clave global del servidor.

Si la clave del servidor no está configurada, la aplicación falla cerrada y no muestra datos. Nunca incluyas esa clave dentro del instalador.

## Importar la biblioteca existente (antes de distribuir)

Los datos que quedaron en el computador original no se suben automáticamente. Para migrarlos a la biblioteca central:

1. En la app local actual, abre Ajustes → Copia de seguridad → Preparar respaldo y descarga el ZIP de la biblioteca existente.
2. En Render, activa temporalmente GIOVAL_ALLOW_INITIAL_IMPORT=true y guarda el cambio.
3. Entra a la aplicación central con la clave compartida, abre Ajustes → Copia de seguridad → Importar respaldo inicial al espacio compartido, carga el ZIP y confirma. El importador acepta solo una biblioteca central vacía, valida la integridad SQLite y rechaza rutas ZIP inseguras; no sobrescribe una biblioteca que ya tenga datos.
4. Vuelve a Render y desactiva inmediatamente GIOVAL_ALLOW_INITIAL_IMPORT=false.
5. Confirma que el catálogo e historial migrados se ven desde el servicio central antes de compartir el instalador.

Las opciones de importación inicial permanecen ocultas mientras la variable esté desactivada.

## Respaldos y operaciones

Usa Ajustes → Copia de seguridad periódicamente desde el servicio central y guarda el ZIP fuera de Render. La biblioteca usa una sola instancia de servicio y un disco persistente; el plan debe mantenerse activo para que el equipo acceda a la aplicación.

## Compilar el cliente Windows manualmente

En Windows x64, con Python instalado para desarrollo:

    python -m pip install -r requirements-desktop.txt
    python -m PyInstaller --noconfirm --clean gioval_desktop.spec

El cliente se conecta al dominio central indicado en desktop_launcher.py (o a la variable de entorno GIOVAL_CLOUD_URL). No genera ni conserva una biblioteca comercial en el equipo cliente.
