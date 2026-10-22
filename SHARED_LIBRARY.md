# Gioval: biblioteca compartida

Gioval puede compartir una única biblioteca entre los usuarios. **Si no tienes presupuesto, utiliza el servidor propio gratuito descrito en [GIOVAL_GRATIS.md](GIOVAL_GRATIS.md).** Un computador Windows que ya tengas ejecuta el servidor y guarda el catálogo de productos, las fotos, las fichas técnicas y el historial comercial de cotizaciones. Los demás equipos abren ese mismo servidor.

## Alternativa alojada en Render (pago)

El archivo `render.yaml` configura un servicio de Render con disco persistente. Este disco requiere un servicio web de pago; no pulses Apply si no quieres contratar ese plan. La modalidad gratuita de Render pierde los cambios del sistema de archivos al reiniciar o suspenderse, así que no debe usarse como única copia de la biblioteca.

Para la ruta gratuita, sigue paso a paso [GIOVAL_GRATIS.md](GIOVAL_GRATIS.md).

## Acceso y datos

`GIOVAL_REQUIRE_ACCESS=true` activa el acceso protegido y el modo de privacidad de la biblioteca compartida. En ese modo se comparten:
- catálogo, códigos, alias, precios/costos/márgenes, fotos y fichas técnicas;
- historial de cotizaciones con número, fecha, productos, cantidades, precios, totales y estado.

Los nombres, RUT, direcciones, teléfonos, correos y las notas de clientes no se guardan en el historial compartido. Cada trabajador escribe los datos del cliente en su sesión cuando genera su cotización. El PDF y el Excel resultantes se descargan desde la sesión, pero no se archivan en la biblioteca común. Por este motivo, al reabrir una cotización histórica será necesario volver a ingresar los datos de cliente y regenerar los documentos.

Al activar este modo por primera vez, Gioval sanea los registros históricos existentes: borra identificadores y notas de clientes, elimina los registros guardados de clientes y borra los antiguos PDF/Excel de la carpeta compartida porque podrían contener datos personales. Conserva los productos, las líneas comerciales y los totales. Haz una copia de seguridad de la instalación de origen antes de importarla.

La clave `GIOVAL_ACCESS_PASSWORD` sigue siendo compartida por los dos usuarios; no identifica a cada persona. Compártela únicamente con trabajadores autorizados.

La clave de OpenAI es opcional. Si se quieren usar funciones de IA, configura `OPENAI_API_KEY` solo en el servidor. No es necesaria para crear cotizaciones usando el flujo manual.

## Importar la biblioteca existente

Si vas a trasladar una biblioteca local ya utilizada, haz primero un respaldo desde Ajustes → Copia de seguridad → Preparar respaldo. Luego usa la importación inicial descrita en la modalidad elegida. En el servidor compartido se conservarán los datos comerciales; por privacidad, los datos de cliente y los PDF/Excel archivados se eliminarán al terminar la migración. La biblioteca original no se modifica por preparar el respaldo, así que consérvala hasta validar la importación.

## Respaldos

Usa Ajustes → Copia de seguridad periódicamente y guarda el ZIP fuera del computador que aloja la biblioteca. Para la modalidad gratuita, no sincronices SQLite en tiempo real con Google Drive/OneDrive; copia únicamente los ZIP de respaldo. Los respaldos del modo compartido contienen datos comerciales y productos, no la libreta de clientes ni los archivos PDF/Excel con datos personales.
