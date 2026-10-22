# Gioval: biblioteca compartida

Gioval puede compartir una única biblioteca entre todos los usuarios. **Si no tienes presupuesto, utiliza primero el servidor propio gratuito descrito en [GIOVAL_GRATIS.md](GIOVAL_GRATIS.md).** Un computador Windows que ya tengas ejecuta la aplicación y guarda la base SQLite, fotos, fichas, Excel y PDF; los demás equipos abren el mismo servidor.

## Alternativa alojada en Render (pago)

El archivo `render.yaml` configura un servicio de Render con disco persistente. Este disco requiere un servicio web de pago; no pulses Apply si no quieres contratar ese plan. La modalidad gratuita de Render pierde los cambios del sistema de archivos al reiniciar o suspenderse, así que no debe usarse como única copia de la biblioteca.

Para la ruta gratuita, sigue paso a paso [GIOVAL_GRATIS.md](GIOVAL_GRATIS.md).

## Acceso y datos

`GIOVAL_REQUIRE_ACCESS=true` protege la aplicación con la clave `GIOVAL_ACCESS_PASSWORD`. Quien tenga esa clave puede consultar y editar toda la biblioteca, incluidos los datos de clientes y cotizaciones. Es una clave compartida, no identifica a cada persona; si más adelante se necesita revocar usuarios individualmente y tener auditoría por persona, habrá que implementar cuentas individuales.

La clave de OpenAI es opcional. Si se quieren usar funciones de IA, configura `OPENAI_API_KEY` solo en el servidor. No es necesaria para crear cotizaciones usando el flujo manual.

## Importar la biblioteca existente

Si vas a trasladar una biblioteca local ya utilizada, haz primero un respaldo desde Ajustes → Copia de seguridad → Preparar respaldo. Luego copia el respaldo al servidor y usa la opción de importación inicial descrita en la documentación de la modalidad que elijas. No borres la biblioteca original hasta confirmar que el catálogo, clientes, historial, archivos Excel y PDF se ven bien en el servidor.

## Respaldos

Usa Ajustes → Copia de seguridad periódicamente y guarda el ZIP fuera del computador o servicio que aloja la biblioteca. Para la modalidad gratuita, la guía recomienda no sincronizar en tiempo real el archivo SQLite con servicios de sincronización de carpetas; copia únicamente los ZIP de respaldo.
