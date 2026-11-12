# Base del catálogo SEO/GEO de Gioval

## Alcance de esta entrega

Gioval opera como cotizador y biblioteca privada. La app ahora incorpora IDs persistentes y edición de fichas públicas SEO/GEO. El código no migra la biblioteca por sí solo: los UUID de productos existentes se escriben cuando una persona ejecuta la acción explícita desde Biblioteca de productos. No crea una tienda ni publica un sitio.

El exportador solo incluye productos cuya ficha tenga estado publicacion.estado = aprobado. Si faltan la identidad global, descripción, URL estable o una imagen propia/licenciada aprobada, la ficha se omite y el motivo se informa sin imprimir costos ni precios privados.

## Datos públicos separados de la operación

Cada entrada del índice puede incorporar dos campos nuevos:

- id_global: UUID persistente para relacionar el mismo producto entre Giovalia, Inventario, Radar e Importex.
- publicacion: ficha destinada a la web. Debe contener estado, slug, nombre, descripción, marca, categoría, código público opcional, código de fabricante opcional, GTIN confirmado opcional, especificaciones e imágenes aprobadas.

Cada imagen publicada necesita URL HTTPS, texto alternativo, aprobación y derechos con valor propia o licenciada.

La oferta es opcional. Solo se exporta si publicacion.oferta.estado = aprobada y existen precio mayor que cero, moneda, disponibilidad reconocida, fuente HTTPS, fecha de verificación, fecha futura de vigencia y base tributaria declarada. El catálogo interno mantiene precio_neto, costo_neto, margen_pct y notas fuera de la salida pública.

Estados recomendados para publicacion.estado: borrador, pendiente_revision, aprobado, retirado. Solo aprobado se exporta. No se debe cambiar el precio de cotización a oferta pública por defecto.

## Flujo integrado en Giovalia

1. Respaldar la biblioteca desde la app.
2. Abrir **Biblioteca de productos**. Los productos nuevos reciben ID global al crearse; para los existentes, usar **Preparar identidad global** y asignar los UUID faltantes. La acción no cambia nombres, alias, precios, costos, archivos ni cotizaciones.
3. Abrir el producto y completar **Ficha pública SEO/GEO**. Mantenerla como borrador mientras falten pruebas. Las imágenes necesitan URL HTTPS accesible, texto alternativo, derechos declarados y aprobación.
4. Aprobar solo cuando los datos estén verificados y la página que los mostrará esté definida. La oferta pública es opcional y no se rellena desde el último precio de cotización.
5. En **Exportar JSON-LD para una web**, usar el dominio HTTPS oficial confirmado, preparar el archivo y revisar los productos incluidos y omitidos.
6. Comparar el archivo con la página antes de publicarlo. No se incluyen cantidades de stock, costos, márgenes, historial, ni datos de clientes.

El script preparar_ids_catalogo.py queda como alternativa para preparar una copia offline, por ejemplo para revisar una migración. Esos UUID no se vuelven IDs de la biblioteca activa hasta que se adopten por un flujo persistente. Para usar el exportador por consola:

    python catalogo_publico.py --input revision/indice_con_ids.json --output revision/catalogo_publico.json --base-url https://DOMINIO-VALIDADO

## Integración con los proyectos

- Inventario guarda el UUID de Gioval como una relación manual opcional. No se debe rellenar por coincidencia de nombre, alias o precio.
- Radar conserva su catálogo oficial de Convenio Marco separado y puede asociar oportunidades al UUID de Gioval después de confirmar equivalencia.
- Importex conserva observaciones de mercado y ofertas de proveedores como evidencia, no como oferta pública. La asociación al UUID debe ser revisada.
- La biblioteca de cotizaciones sigue siendo privada. No exportar bases SQLite, historial, datos de clientes, costos, márgenes ni referencias privadas.

## Plantilla editorial para una ficha de producto

Completa solo atributos que estén respaldados por una fuente identificable. Conserva junto a cada dato su fuente, fecha de verificación y responsable de aprobación en el registro interno; exporta a la página solo la información que corresponda mostrar al cliente.

- **Identidad:** nombre de producto, marca, modelo/código de fabricante y variante específica. No juntes variantes de tamaño, color o compatibilidad en una ficha si cambian la decisión de compra.
- **Respuesta de compra:** qué es, para quién sirve, uso recomendado y necesidad que resuelve, en lenguaje concreto.
- **Especificaciones:** dimensiones, material, formato, contenido del paquete, compatibilidades, requisitos, certificaciones y garantía, cuando estén confirmados. Usa pares nombre/valor en el campo especificaciones.
- **Confianza:** imágenes reales y autorizadas, tabla de medidas/compatibilidad si aplica, política de garantía/devolución solo cuando esté confirmada y preguntas frecuentes que respondan dudas reales.
- **Oferta:** precio visible, moneda, impuestos, disponibilidad y vigencia solo si el negocio realmente ofrece esos datos en la página. El último precio cotizado no sustituye una oferta pública.

La página futura debe mostrar los mismos datos que el JSON-LD. Usa títulos únicos, una URL estable por producto/variante, enlaces desde categorías y navegación HTML rastreable. Las preguntas frecuentes ayudan a las personas y a respuestas generativas cuando son útiles; no prometen un resultado enriquecido ni una cita de IA.

## SEO, GEO y publicación

El JSON-LD solo describe datos de una página pública. La página debe mostrar la misma información, imágenes, precio y disponibilidad. No publicar el JSON-LD por sí solo ni asumir que genera resultados enriquecidos o citas de IA.

Para habilitar resultados de producto, crear después páginas rastreables por producto y categoría, con URL canónica, navegación HTML, sitemap y datos estructurados validados. El tipo de resultado depende de si la página permite comprar directamente o solo informa/recibe solicitudes de cotización. La indexación y la visibilidad en respuestas de IA se miden después del despliegue; la generación del archivo solo prueba que la capa de datos es consistente.
