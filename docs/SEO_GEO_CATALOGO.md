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
3. Revisar que los UUID permanezcan asociados a la misma entidad. Esta migración no combina productos ni cambia claves, alias, precios o archivos.
4. Completar y aprobar las fichas publicacion por fuente verificable.
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

## SEO, GEO y publicación

El JSON-LD solo describe datos de una página pública. La página debe mostrar la misma información, imágenes, precio y disponibilidad. No publicar el JSON-LD por sí solo ni asumir que genera resultados enriquecidos o citas de IA.

Para habilitar resultados de producto, crear después páginas rastreables por producto y categoría, con URL canónica, navegación HTML, sitemap y datos estructurados validados. El tipo de resultado depende de si la página permite comprar directamente o solo informa/recibe solicitudes de cotización. La indexación y la visibilidad en respuestas de IA se miden después del despliegue; la generación del archivo solo prueba que la capa de datos es consistente.
