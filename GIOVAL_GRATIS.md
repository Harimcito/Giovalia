# Gioval sin costo mensual: servidor propio

## Objetivo

Compartir una sola biblioteca entre todos los usuarios sin pagar alojamiento. Un computador Windows que ya tengas funciona como servidor central. La aplicación, la base SQLite y los archivos de cotizaciones permanecen en ese computador; los demás equipos se conectan por la red, sin guardar copias comerciales independientes.

**Costo adicional de plataforma: $0.** Se usa un equipo, conexión a internet y dominio que ya tengas. No es un servicio con garantía de disponibilidad: si el computador central se apaga, duerme o pierde internet, la aplicación deja de estar disponible hasta que vuelva a funcionar.

## 1. Preparar el computador central (Windows)

1. Elige un computador fiable que pueda quedarse encendido durante el horario de trabajo.
2. Instala Python 3.12 desde https://www.python.org/downloads/ y activa **Add python.exe to PATH**. No necesitas instalar Python en los computadores que solo usarán el cliente de escritorio.
3. Descarga el código de este repositorio como ZIP y descomprímelo en una carpeta fija; por ejemplo, `C:\Gioval\Giovalia`. También puedes usar el repositorio clonado que ya tengas.
4. En esa carpeta, ejecuta `instalar_servidor_gioval.bat` una sola vez. Instala las dependencias en el perfil de Windows, fuera del repositorio.
5. Ejecuta `iniciar_servidor_gioval.bat`. La primera vez pedirá una clave de acceso compartida de al menos 12 caracteres. La clave se guarda cifrada con las herramientas de Windows para el mismo usuario; no se sube a GitHub. Comparte la clave solo con el equipo autorizado.
6. Mantén abierta la ventana del servidor. La dirección en el propio computador es `http://localhost:8501`.

### Importar datos ya existentes

1. En tu instalación actual, abre Ajustes → Copia de seguridad → Preparar respaldo y descarga el ZIP.
2. Copia el ZIP al computador central.
3. Detén el servidor con Ctrl+C. Vuelve a iniciarlo una sola vez con `iniciar_servidor_gioval.bat -PermitirImportacionInicial`.
4. En Gioval, abre Ajustes → Copia de seguridad → Importar respaldo inicial, carga el ZIP y confirma.
5. Detén el servidor y vuelve a iniciarlo normalmente con `iniciar_servidor_gioval.bat` (sin argumento). Así la importación inicial vuelve a quedar desactivada.

Los datos se guardan en `%LOCALAPPDATA%\Gioval\Datos\biblioteca`, fuera de la carpeta del código. Actualizar o volver a descargar la aplicación no debería reemplazar esa biblioteca.

## 2. Conectar equipos de la misma oficina

1. Asegúrate de que los computadores estén conectados a la misma red local.
2. En el computador central, abre una consola y ejecuta `ipconfig`. Anota su dirección IPv4 (por ejemplo, `192.168.1.20`).
3. Si Windows Firewall pregunta si permites conexiones para Python, permite únicamente la red privada de confianza. No crees reglas de acceso público ni abras el puerto 8501 en el router.
4. En cada computador cliente, instala `Gioval-Setup.exe`. En el menú Inicio quedará **Configurar conexión de Gioval**. Ejecuta esa opción e introduce `http://192.168.1.20:8501`, reemplazando la IP por la del servidor.
5. Abre Gioval e introduce la clave compartida.

Conviene reservar la dirección IP del computador central en el router para que no cambie. Si cambia la IP, vuelve a configurar cada cliente.

## 3. Conectar desde fuera de la oficina (opcional, sin alojamiento de pago)

Para acceder desde otras redes puedes publicar el servidor mediante **Cloudflare Tunnel**, que permite conectar un servicio propio sin exponer una IP pública ni abrir puertos entrantes. El túnel está disponible en todos los planes de Cloudflare; consulta la documentación oficial: https://developers.cloudflare.com/tunnel/.

Esto requiere un dominio activo configurado en Cloudflare. Si ya utilizas `gioval.cl`, puedes crear un subdominio como `cotizador.gioval.cl` y dirigirlo a `http://localhost:8501`. La URL de los clientes será HTTPS, por ejemplo `https://cotizador.gioval.cl`.

**Importante para el correo empresarial:** antes de cambiar los servidores DNS de `gioval.cl`, copia y verifica todos los registros existentes (MX, SPF, DKIM, DMARC, CNAME, A y TXT). Un cambio de DNS incompleto puede interrumpir el correo o la landing page. No cambies los servidores DNS hasta tener inventariados los registros actuales. Cloudflare Tunnel necesita que el dominio esté configurado con Cloudflare para publicar un nombre de host: https://developers.cloudflare.com/tunnel/get-started/.

La clave de Giovalia sigue siendo obligatoria. Para una capa adicional, Cloudflare Access ofrece un plan Zero Trust gratuito para equipos de menos de 50 usuarios, aunque su configuración requiere una cuenta propia: https://www.cloudflare.com/es-la/sase/products/access/.

## 4. Respaldos

- Desde Giovalia, abre Ajustes → Copia de seguridad → Preparar respaldo y guarda el ZIP.
- Mantén una copia adicional fuera del computador central: un disco externo o almacenamiento gratuito que ya uses.
- Haz el respaldo regularmente y antes de actualizar la aplicación o cambiar de equipo.
- Nunca pongas la carpeta `biblioteca` dentro de una carpeta de Google Drive/OneDrive sincronizada mientras la app está escribiendo directamente la base SQLite. La aplicación debe trabajar con una única copia activa de la base; sincroniza solo los respaldos ZIP.

## Limitaciones de esta modalidad gratuita

- El computador central debe estar encendido, conectado y sin suspensión.
- Si se daña el disco del servidor, necesitas el respaldo para recuperar la información.
- Por red local, los clientes deben estar en la misma red; desde fuera se necesita configurar el túnel.
- Esta modalidad no proporciona alta disponibilidad ni soporte de proveedor, pero evita la tarifa mensual de hosting y mantiene la biblioteca en un equipo bajo tu control.
