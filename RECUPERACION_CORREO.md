# Recuperación de contraseña por correo

Giovalia migra automáticamente la base local de usuarios al iniciar y añade los campos de correo y verificación. La cuenta de Harim debe iniciar sesión una vez para configurar los correos.

## Crear una cuenta

1. En la pantalla de acceso, abre **Crear cuenta** y completa nombre, correo y contraseña.
2. Giovalia envía un código al correo. Ingresa el código en **Verificar correo** para activar la cuenta.
3. Inicia sesión usando el correo y la contraseña. Las cuentas creadas desde el formulario reciben el rol de trabajador; el formulario no permite crear administradores.

Si el código vence, usa **Reenviar código de activación**. Por seguridad, solo se puede enviar un código por cuenta cada 60 segundos.

Las contraseñas nuevas requieren al menos 12 caracteres alfanuméricos, con letras y números. El correo debe verificarse antes del primer acceso.

## Asociar y verificar las cuentas

1. En Giovalia, abre **Mi cuenta y contraseña**.
2. En **Administrar cuentas y correos de recuperación**, escribe un correo distinto para cada cuenta y guarda.
3. Pulsa **Enviar código de verificación**. Cada titular debe iniciar sesión, abrir **Mi cuenta y contraseña** e ingresar el código recibido.
4. La recuperación solo se habilita para correos verificados.

Si se cambia el correo asociado, la verificación anterior se invalida y hay que verificar la dirección nueva.

## Configurar el correo saliente

El servidor envía los códigos por SMTP con TLS. Configura estas variables en el entorno del proceso que ejecuta Giovalia; en un proveedor de alojamiento, guárdalas como secretos del servicio:

- `GIOVAL_SMTP_HOST`: servidor SMTP.
- `GIOVAL_SMTP_PORT`: puerto, normalmente 587 para STARTTLS o 465 para SSL.
- `GIOVAL_SMTP_USER`: cuenta remitente autenticada.
- `GIOVAL_SMTP_PASSWORD`: contraseña de aplicación o credencial SMTP.
- `GIOVAL_SMTP_FROM`: dirección remitente autorizada.
- `GIOVAL_SMTP_SECURITY`: `starttls` (predeterminado) o `ssl`.

No guardes credenciales SMTP en el repositorio ni en archivos de configuración compartidos. Reinicia el proceso de Giovalia después de configurar las variables.

## Flujo de recuperación

En la pantalla de inicio de sesión, **¿Olvidaste tu contraseña?** solicita un código al correo verificado. El código vence en 30 minutos, se almacena solo como hash y se invalida al usarlo, al cambiar el correo o al solicitar otro. Hay un límite de un envío por cuenta cada 60 segundos. La pantalla no indica si una dirección está registrada.

Al completar la recuperación, Giovalia reemplaza la contraseña, cierra las sesiones anteriores y limpia los intentos fallidos. La nueva contraseña debe tener al menos 12 caracteres alfanuméricos e incluir letras y números.
