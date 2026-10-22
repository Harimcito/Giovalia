# Cotizador: cotización + fotos + fichas técnicas

Dos formas de usarlo:

1. **Tengo la cotización en Excel** → entrega un PDF único: cotización (con tu formato y logo)
   + anexo de fotos + fichas técnicas al final, con marcadores para navegar.
2. **Tengo una orden en bruto** (solo productos y cantidades, pegada como texto o en Excel/CSV)
   → crea la cotización con tu plantilla (`plantilla_cotizacion.xlsx`), y luego sigue igual que (1).

## App de escritorio y biblioteca compartida

El instalador de Windows abre Gioval en una ventana independiente y se conecta a un servidor central. La opción sin costo mensual está descrita en [GIOVAL_GRATIS.md](GIOVAL_GRATIS.md): un computador Windows existente guarda la biblioteca y los demás equipos se conectan por red local o, opcionalmente, mediante un túnel HTTPS gratuito. No sincronices la base SQLite en tiempo real mediante carpetas de Google Drive/OneDrive.

Para la alternativa alojada, consulta [SHARED_LIBRARY.md](SHARED_LIBRARY.md). No necesitas contratar Render para usar la modalidad gratuita.

## Radar Comercial (aplicación independiente)

Gioval incluye una segunda aplicación para detectar oportunidades de Compra Ágil y estimar rentabilidad a partir del catálogo comercial del cotizador. Consulta [RADAR_COMERCIAL.md](RADAR_COMERCIAL.md) para instalación y uso.

- En el computador central ejecuta `iniciar_radar_gioval.bat`; el cotizador permanece en el puerto 8501 y Radar corre por separado en el puerto 8502.
- Abre `http://localhost:8502` en el servidor o `http://IP-DEL-COMPUTADOR-CENTRAL:8502` desde otro equipo de la misma red.
- Radar lee el catálogo y el historial del cotizador, pero guarda las oportunidades y las evaluaciones en `radar_comercial.db`, sin modificar las cotizaciones existentes.
- La consulta directa a la API requiere un ticket propio de ChileCompra. También se pueden importar resultados desde Excel/CSV o registrar una oportunidad manualmente.

## Instalación en Windows (una vez)
1. Python 3.10 o superior desde python.org (marca "Add python.exe to PATH" al instalar).
2. (Opcional) LibreOffice, solo si quieres un PDF con formato idéntico al Excel (casilla en la barra lateral).
3. Descomprime esta carpeta donde quieras.

## Uso
Doble clic en `iniciar_cotizador.bat`. En cada inicio verifica que estén los componentes
(la primera vez o tras una actualización tarda un par de minutos).

## Fotos y fichas desde internet
En el paso 3 presiona «Buscar en internet lo que falta». Para cada producto verás las fotos
y fichas encontradas con vista previa: elige con «Usar». Quedan guardadas en `biblioteca/`
y no se vuelven a buscar. Si la búsqueda falla o no encuentra nada: cambia el texto de
búsqueda, pega el enlace directo de una imagen o PDF, o usa los botones de Google.
**Revisa siempre que la foto y la ficha correspondan exactamente al producto.**

## Revisión, historial, clientes y catálogo
- **Revisión:** antes de crear el PDF comprueba que el neto sea la suma de las líneas, que el IVA sea el
  19 %, que no falten precios, que no haya productos repetidos o vendidos bajo tu costo, que el precio no
  se aleje mucho del último usado y que el RUT del cliente sea válido. Los errores graves piden confirmación.
- **Historial:** cada cotización queda guardada con su número correlativo (formato editable en ⚙️ Ajustes →
  Numeración), su estado (Borrador, Enviada, Aceptada, Rechazada), su Excel y su PDF. Puedes duplicarla como
  base de una nueva o seguir editándola conservando el número.
- **Clientes:** en una instalación local independiente se guardan y se pueden reutilizar. En el modo de biblioteca compartida no se guardan datos identificatorios; cada trabajador los introduce al preparar cada documento.
- **Mis productos:** catálogo con foto, ficha, último precio, costo y margen. Precio sugerido = costo × (1 + margen);
  al armar un pedido esos productos aparecen con su precio ya puesto. Aquí también se cambia el código,
  se combinan duplicados y se eliminan productos.
- **Biblioteca compartida:** el servidor central comparte productos, fotos, fichas y el historial comercial de cotizaciones (líneas, cantidades, precios, totales y estado). Los datos del cliente y los PDF/Excel finales no se guardan en el historial común; los documentos se descargan desde la sesión que los genera.
- Para respaldar el modo local, copia la biblioteca. Para el modo compartido, usa Ajustes → Copia de seguridad y conserva los ZIP fuera del computador central.

## Cómo busca ahora
- Todo producto se puede buscar: si no tiene código de fabricante, la app crea una clave
  descriptiva (p. ej. SACA-CORCHETES-PALANCA-METALICO) y la recuerda.
- Con la API key de OpenAI, la IA lee cada descripción y deduce marca, tipo, código del
  fabricante y los mejores textos de búsqueda (si no está segura del código, no lo inventa).
  La columna «Origen» muestra de dónde salió cada clave: 📚 aprendida, 🧠 IA, 🔤 del texto,
  ✏️ por descripción.
- Sin API key, limpia la descripción (quita «original», «compatible con impresora»…).
- Cada producto tiene dos cajas editables: «Buscar la foto como» y «Buscar la ficha como».
- Si una búsqueda no da resultados prueba hasta 5 variantes; si aun así falla, te dice cuántos
  resultados hubo y por qué se descartaron. Como último recurso usa miniaturas (baja resolución).
- Qué ayuda más: marca + código/modelo. Para consumibles, el modelo de la impresora.
  Para librería, marca + tipo + medida o capacidad.

## Que la ficha corresponda al producto
- Cada ficha encontrada se lee y se comprueba que nombre el código del producto:
  ✅ lo nombra, 🟡 lo nombra pero es un documento largo (posible catálogo), ⚠️ no lo nombra.
- Las fotos se marcan «coincide con el código» cuando el título, la página o el enlace lo nombran.
- «Sacar la foto desde esta ficha» usa una imagen de la propia ficha: coincide seguro.
- Al generar el PDF te avisa si alguna ficha guardada no nombra el código de su producto.

## Orden en bruto
- Texto: una línea por producto, p. ej. `10 Tóner HP CF283A`, `15x TK-3182`, `Resma carta x5`.
- Excel/CSV: con encabezados (Cantidad, Descripción/Producto) o sin ellos.
- PDF o foto (puedes subir varias): se leen con IA (OpenAI, modelo gpt-4o). Necesita tu API key.
  El documento se envía a OpenAI. Revisa siempre lo leído en la tabla antes de crear la cotización.
- Elige si los precios que ingresas llevan IVA o son netos. El Neto es la suma de las líneas,
  el IVA es 19 % y el Total es Neto + IVA.
- Los precios se recuerdan por producto y se autocompletan la próxima vez.
- Para cambiar el encabezado, logo o forma de pago, edita `plantilla_cotizacion.xlsx`.

## Dónde poner la API key de OpenAI (una sola vez)
La app la busca, en este orden: variable de entorno `OPENAI_API_KEY`, el archivo
`configuracion.py` (pega la clave entre las comillas y reinicia), y el archivo `.env`.
Si no encuentra ninguna, la pides en la barra lateral y el botón «Guardar esta clave en este
computador» la guarda en `.env` para siempre. No compartas esta carpeta con la clave dentro.

## La biblioteca
    biblioteca/CF283A/foto.jpg
    biblioteca/CF283A/ficha.pdf
    biblioteca/indice.json   (claves, alias aprendidos y últimos precios)
