# GiovalIA: servidor principal con perfiles

La modalidad gratuita ejecuta una sola instancia del cotizador en el PC principal.
El inventario y las cotizaciones permanecen en `%LOCALAPPDATA%\Gioval\Datos\biblioteca`.
El código se instala en `%LOCALAPPDATA%\Programs\Giovalia-Servidor`, separado de la
instalación anterior para evitar que su actualizador reemplace esta versión.

## Uso

- Abrir el acceso directo **Gioval** o **GiovalIA compartida** del Escritorio.
- En este PC: `http://localhost:8501`.
- Perfiles: `harim` (administrador) y `giovanna` (trabajadora). Ambos comparten todos los datos.
- Las contraseñas temporales se entregan localmente en
  `%LOCALAPPDATA%\Gioval\Acceso\Accesos-iniciales.txt`, protegido por permisos de Windows.
- El primer ingreso exige elegir una contraseña de al menos 6 caracteres, con letras y números,
  sin espacios ni símbolos. No compartir contraseñas en mensajes o GitHub.
- Ajustes permite cambiar la contraseña propia; Harim puede restablecer o deshabilitar la de Giovanna.
- Cerrar sesión borra el estado de trabajo de ese navegador. Los borradores guardados siguen disponibles.
- Actualizar datos compartidos recarga catálogo e historial; no descarta la edición de una cotización.
- Las versiones antiguas de cotizaciones no pueden sobrescribir guardados más recientes.

## Arranque y disponibilidad

Un acceso directo en la carpeta Inicio de Windows inicia el supervisor al iniciar sesión.
El PC debe permanecer encendido, con la sesión de Windows iniciada y sin suspensión mientras se use.
No se configura inicio de sesión automático de Windows ni se cambia su contraseña.
El supervisor comprueba el proceso cada 30 segundos y lo reinicia si termina o deja de responder.
No descarga actualizaciones durante las cotizaciones.

La instalación inicial no modifica las reglas de Windows ni abre puertos del router.
Para el túnel HTTPS basta acceder a `127.0.0.1`; la instalación reproducible nueva se limita a esa dirección.
No es necesario cambiar el perfil de red de Windows para el acceso mediante túnel.
Radar tiene la misma pantalla de perfiles integrada; su arranque automático permanece desactivado
hasta que se solicite incorporarlo a la operación.

## Acceso desde otro computador

La dirección definitiva prevista es `https://app.gioval.cl`. Requiere completar:

1. Acceso al panel que contiene los registros DNS reales de gioval.cl.
2. Exportar y comprobar TODOS los registros actuales, incluidos sitio y correo.
3. Incorporar el dominio al plan gratuito de Cloudflare sin cambiar los servicios existentes.
4. Crear un túnel permanente a `http://127.0.0.1:8501` y configurar app.gioval.cl.
5. Instalar el conector oficial en este PC con inicio automático.
6. Verificar HTTPS, inicio de sesión y una cotización compartida desde la otra red.

No basta con abrir una sesión de Hosty vacía: debe aparecer el servicio que administra el dominio.
Hasta completar estos pasos, localhost funciona solo en este PC. No hay enlace provisional publicado.
Supabase se pospone por decisión del usuario; esta fase usa almacenamiento central en este PC.

## Respaldos y restauración

El supervisor genera un ZIP verificado al día en `%LOCALAPPDATA%\Gioval\Backups\Servidor`.
Incluye biblioteca, documentos, Radar si existe y base de perfiles, sin sesiones de acceso.
No incluye claves API ni contraseñas temporales. Conserva una copia adicional en otro disco o equipo:
el respaldo automático local no protege frente a pérdida del disco del PC principal.

Para un respaldo manual:

```powershell
& "$env:LOCALAPPDATA\Gioval\servidor-venv\Scripts\python.exe" .\respaldo_servidor.py "$env:LOCALAPPDATA\Gioval" "D:\RespaldosGioval"
```

Para verificar una recuperación, `restaurar_servidor.py` solo acepta un destino nuevo, nunca una
biblioteca activa. Reescribe las rutas de los documentos y verifica la integridad de SQLite.

```powershell
python restaurar_servidor.py "D:\RespaldosGioval\giovalia-fecha.zip" "D:\GiovalRecuperado"
```

El historial comercial y sus PDF/Excel se conservan en el modo `GIOVAL_PROFILES=true`.
La política antigua de anonimización solo sigue vigente en instalaciones heredadas sin perfiles.
Nunca iniciar simultáneamente el antiguo servidor y el nuevo sobre la misma biblioteca.

## Instalación y mantenimiento

`instalar_servidor_principal.ps1` reutiliza el entorno Python ya instalado, respalda los datos,
prepara perfiles que aún no existan y crea accesos directos. No reemplaza contraseñas existentes.
Antes de reinstalar una versión activa, cerrar sus procesos de forma controlada tras guardar el trabajo.
Para actualizaciones, respaldar, verificar la versión en una copia y reiniciar fuera del trabajo en curso.

Diagnóstico: `%LOCALAPPDATA%\Gioval\logs\servidor-principal.log` y `principal-cotizador.log`.
El modo de pruebas con Agent API se usa únicamente en localhost y nunca se habilita en producción.

Si Harim olvida su contraseña, el propietario de la sesión de Windows puede ejecutar
`recuperar_acceso.py harim` con el Python instalado. La contraseña se introduce localmente,
no se imprime, revoca las sesiones anteriores y exige cambiarla al siguiente ingreso.
