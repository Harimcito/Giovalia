# Gioval · Radar Comercial (aplicación independiente)

## Para qué sirve

Radar Comercial es una segunda aplicación Streamlit, separada del cotizador. Está diseñada para los dos trabajadores de Gioval y ayuda a descubrir Compra Ágil, evaluar si el catálogo puede responder a los productos solicitados y estimar ingresos, costos y margen antes de preparar una oferta.

No sustituye Mercado Público ni envía ofertas. La persona usuaria debe revisar el proceso y presentar la cotización en la plataforma oficial.

## Conexión con el cotizador

- Lee el catálogo desde `GIOVAL_DATA_DIR/biblioteca/indice.json`: códigos, descripciones, alias, precios, costos y márgenes.
- Lee el historial comercial desde `GIOVAL_DATA_DIR/biblioteca/cotizador.db`, utilizando solo cantidades, estados, importes y líneas de producto para métricas agregadas.
- No escribe ni modifica las cotizaciones del cotizador.
- Guarda oportunidades, seguimiento, notas comerciales y evaluaciones económicas en una base separada: `GIOVAL_DATA_DIR/radar_comercial.db`.
- No guarda el ticket de ChileCompra en ningún archivo o base de datos. El ticket se usa desde la sesión del navegador para hacer la solicitud.
- La proyección de detalle de Compra Ágil excluye información de proveedores y sus RUT. No ingreses datos personales de clientes en las notas de oportunidades.

Si se ejecuta mediante el servidor oficial del proyecto, ambas aplicaciones usan la misma biblioteca ubicada en `%LOCALAPPDATA%\Gioval\Datos\biblioteca`.

## Cómo iniciar la segunda aplicación en Windows

En el computador central donde ya se ejecuta el cotizador:

1. Actualiza el repositorio a la versión que incluye `radar_comercial`.
2. Si Gioval ya está instalado, no hace falta crear otro entorno Python; Radar utiliza el entorno `servidor-venv` existente.
3. Ejecuta `iniciar_radar_gioval.bat`.
4. Deja abierta esa ventana mientras quieras que Radar esté disponible. El cotizador sigue en el puerto 8501 y Radar escucha por separado en el puerto 8502.
5. En el computador central abre `http://localhost:8502`. Desde el segundo computador de la misma red, abre `http://IP-DEL-COMPUTADOR-CENTRAL:8502`.

Si Windows Firewall bloquea la conexión, permite el puerto TCP 8502 solamente en el perfil de red **Privada** y para la red de la oficina. No abras el puerto al internet ni configures una regla pública en el router. Para acceso fuera de la oficina, primero debe configurarse un túnel HTTPS por separado para el nuevo puerto.

Ambas aplicaciones piden la clave compartida de Gioval cuando se utiliza el modo de acceso protegido. Se autentican por separado, ya que funcionan como aplicaciones diferentes.

## Ayuda integrada con IA

Radar ofrece una ventana **Ayuda / IA** accesible desde las secciones de la aplicación. Incluye guías rápidas locales y un chat contextual que conoce la sección en que estás. La IA orienta, pero no pulsa controles, modifica registros ni presenta ofertas.

- Si existe la variable de entorno **OPENAI_API_KEY**, o está definida en el archivo **.env** de la raíz del proyecto, el asistente puede usar esa clave.
- También puedes pegar la clave en la ventana de ayuda. En ese caso queda solo en la sesión de Streamlit y no se escribe a disco ni a la base de Radar.
- El modelo predeterminado es **gpt-6-luna**; se puede cambiar con **GIOVAL_RADAR_AI_MODEL** si la cuenta tiene acceso a otro modelo.
- Se envía la pregunta, el historial reciente de ese chat y las instrucciones de uso. No se adjuntan el catálogo, las cotizaciones, radar_comercial.db, el ticket de ChileCompra ni las credenciales de acceso.
- La API de OpenAI se factura aparte de una suscripción a ChatGPT. Sin clave, la búsqueda, importación y evaluación siguen funcionando y queda disponible la guía local; no habrá respuestas generativas libres.

No pegues claves, tickets de API, RUT ni datos personales en las preguntas. Si la conexión falla, comprueba la clave, la cuota/saldo y que el modelo esté habilitado para tu proyecto.

## Conectar con la API oficial Compra Ágil

ChileCompra publicó la API Compra Ágil v2 con los endpoints de listado y detalle, filtros de región/estado/palabras clave, paginación y un mecanismo de ticket. La guía oficial vigente que se tomó como referencia está aquí:

- Portal de API de Mercado Público: https://www.chilecompra.cl/api/
- Guía de uso API Compra Ágil v2 (versión 3.0, mayo de 2026): https://www.chilecompra.cl/wp-content/uploads/2026/05/Documentacion_API_Compra_Agil.pdf

Pasos:

1. Solicita un ticket propio desde el portal oficial usando Clave Única y aceptando las condiciones de uso.
2. En Radar, abre **Conexiones y criterios económicos** y pega el ticket en el campo protegido.
3. Ajusta palabras clave, región y estados. Por defecto se busca en Arica y Parinacota y solo en procesos publicados.
4. Pulsa **Buscar oportunidades**. El Radar hace como máximo ocho búsquedas por operación y consulta una página por palabra para cuidar la cuota diaria.
5. Para una oportunidad seleccionada, puedes solicitar su detalle oficial. La aplicación guarda únicamente los campos comerciales que necesita.
6. Si recibes HTTP 401, revisa el ticket. Si recibes HTTP 429, la guía de ChileCompra indica que se agotó la cuota diaria; espera al siguiente día calendario. Radar no intenta eludir esos límites.

Los tickets identifican a cada solicitante y tienen cuota diaria. No los pegues en GitHub, en notas de oportunidades o en mensajes compartidos. Cada trabajador puede ingresar su propio ticket en su sesión.

## Sin ticket: importar resultados oficiales

El Radar también se puede usar sin conexión directa a la API:

1. Abre el buscador oficial de Compra Ágil en Mercado Público.
2. Descarga los resultados en Excel.
3. En la pestaña **Radar de oportunidades**, carga un archivo XLSX o CSV.
4. Revisa los registros importados, completa los campos que falten y utiliza **Agregar una oportunidad manualmente** cuando necesites guardar un proceso puntual.

Los nombres de columna pueden variar entre exportaciones; la importación reconoce nombres habituales y puede omitir filas que no tengan un título identificable.

## Evaluación económica

El evaluador propone coincidencias textuales entre productos solicitados y el catálogo. La puntuación solo recomienda candidatos: no certifica equivalencia. Para marcar el cálculo como completo se exige confirmar la correspondencia de modelo, presentación, unidad y especificaciones, ingresar precios netos ofertados y revisar costos de despacho y otros gastos.

La preferencia inicial para leer los precios/costos del catálogo es **Incluyen IVA**, de acuerdo con la base que Gioval indicó previamente. Se puede cambiar en **Conexiones y criterios económicos** sin reescribir la biblioteca. El Radar convierte los importes seleccionados a netos para calcular el costo. Si los datos reales no están en esa base, corrige la selección antes de utilizar la evaluación.

El presupuesto publicado se presenta solo como referencia: confirma en el proceso si incluye IVA, despacho y todos los ítems. El margen objetivo inicial es 0 % (no definido) hasta que Gioval establezca su umbral mínimo. No se asume que una oferta sea rentable o admisible solo porque el cálculo sea positivo.

## Respaldo y actualización

La base de Radar, `radar_comercial.db`, queda junto al directorio de datos central, no dentro del código. Inclúyela en el respaldo del computador central o copia ese archivo con Radar detenido. Sigue utilizando el respaldo existente del cotizador para la biblioteca y las cotizaciones; actualizar el repositorio no debe reemplazar los datos comerciales.

## Arquitectura

- `radar_comercial/app.py`: interfaz independiente en el puerto 8502.
- `radar_comercial/servicio.py`: API de Compra Ágil, proyección de datos públicos, coincidencias de catálogo, cálculo de rentabilidad y persistencia.
- `radar_comercial.db`: seguimiento y evaluaciones, separado de `cotizador.db`.
- `tests/test_radar_comercial.py`: pruebas de integración lógica sin solicitudes reales a ChileCompra.
