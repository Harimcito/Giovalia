# Base del catálogo SEO/GEO de Gioval

## Alcance de esta entrega

Gioval opera hoy como cotizador y biblioteca privada. Este cambio prepara una capa de exportación, pero no crea una tienda, no publica un sitio y no modifica la biblioteca activa.

El exportador solo incluye productos cuya ficha tenga estado publicacion.estado = aprobado. Si faltan la identidad global, descripción, URL estable o una imagen propia/licenciada aprobada, la ficha se omite y el motivo se informa sin imprimir costos ni precios privados.

## Datos públicos separados de la operación

Cada entrada del índice puede incorporar dos campos nuevos:

- id_global: UUID persistente para relacionar el mismo producto entre Giovalia, Inventario, Radar e Importex.
- publicacion: ficha destinada a la web. Debe contener estado, slug, nombre, descripción, marca, categoría, código público opcional, código de fabricante opcional, GTIN confirmado opcional, especificaciones e imágenes aprobadas.

Cada imagen publicada necesita URL HTTPS, texto alternativo, aprobación y derechos con valor propia o licenciada.

La oferta es opcional. Solo se exporta si publicacion.oferta.estado = aprobada y existen precio mayor que cero, moneda, disponibilidad reconocida, fuente HTTPS, fecha de verificación, fecha futura de vigencia y base tributaria declarada. El catálogo interno mantiene precio_neto, costo_neto, margen_pct y notas fuera de la salida pública.

Estados recomendados para publicacion.estado: borrador, pendiente_revision, aprobado, retirado. Solo aprobado se exporta. No se debe cambiar el precio de cotización a oferta pública por defecto.

## Flujo sin sobrescribir datos

1. Respaldar la biblioteca desde la app.
2. Crear una copia de indice.json con preparar_ids_catalogo.py. El script conserva UUID existentes y no modifica el archivo de entrada.
3. Tratar esa salida como una propuesta de migración, no como un ID ya persistido en la biblioteca activa. Revisar que cada UUID quede unido a la misma entidad y adoptar la copia mediante el flujo soportado por Giovalia. No regenerar los UUID desde el índice antiguo: volverían a cambiar. Si la app no conserva campos adicionales al guardar, primero hay que implementar una migración dentro de la app; no reemplazar a mano la biblioteca activa.
4. Completar y aprobar las fichas publicacion con fuentes verificables.
5. Ejecutar catalogo_publico.py con una URL HTTPS real ya validada y una salida nueva.
6. Revisar el JSON exportado antes de conectarlo con una web. No se incluye inventario en cantidades; solo una disponibilidad aprobada.

Ejemplo de preparación de una copia:

    python preparar_ids_catalogo.py --input biblioteca/indice.json --output revision/indice_con_ids.json

Ejemplo de exportación después de revisar las fichas y confirmar el dominio:

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
