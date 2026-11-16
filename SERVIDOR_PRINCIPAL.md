# Giovalia con perfiles de equipo

Activa el modo de perfiles con `GIOVAL_PROFILES=true`. Configura `GIOVAL_USERS_DB` con la ruta del archivo de cuentas y `GIOVAL_DATA_DIR` con la carpeta que contiene la biblioteca.

Para una instalación nueva, ejecuta `python preparar_perfiles.py <carpeta-base>`. Las contraseñas iniciales se guardan en la carpeta de cuentas y se cambian al ingresar. Si ya tienes una instalación con perfiles, conserva su base de cuentas y la biblioteca al actualizar el código.

En Windows, `servidor_principal.py` usa `%LOCALAPPDATA%\Gioval\servidor-principal.json` e inicia el cotizador. Utiliza un solo supervisor para cada biblioteca. `servidor_web.py` es el punto de entrada que habilita la sesión persistente del navegador.

Para actualizar la instalación existente, extrae el paquete de servidor en una carpeta nueva. Cierra las ventanas donde se esté cotizando y ejecuta `actualizar_servidor_principal.ps1` desde la carpeta extraída. El script prepara un respaldo, instala el código y comprueba el arranque. Si falla el arranque, devuelve el programa anterior.

El supervisor guarda respaldos diarios bajo `Backups\Servidor`. Los comandos `respaldo_servidor.py` y `restaurar_servidor.py` usan directorios explícitos de origen y destino.

Consulta [Recuperación por correo](RECUPERACION_CORREO.md) para configurar SMTP. El registro de cuentas nuevas requiere activar `GIOVAL_ALLOW_SIGNUP=true`. Las preferencias de la biblioteca central están disponibles para el administrador.
