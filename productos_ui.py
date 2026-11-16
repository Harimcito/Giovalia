"""Edición de datos de producto para el catálogo y las exportaciones SEO/GEO."""
from datetime import date
from hashlib import sha256
import json

import streamlit as st

import catalogo_publico
import seo_geo


def editar_ficha(bib, clave):
    actual = bib.indice[clave]
    panel = st.expander("Editar ficha y datos SEO/GEO", icon=":material/edit:", key=f"edit_producto_{clave}",
                        on_change="rerun")
    estado = st.session_state
    llave = f"_base_producto_{clave}"
    if not panel.open:
        estado.pop(llave, None)
        return
    base = estado.setdefault(llave, actual.copy())
    version = sha256(json.dumps(base, sort_keys=True).encode()).hexdigest()[:10]
    with panel, st.form(f"ficha_producto_{clave}_{version}"):
        nombre = st.text_input("Nombre del producto", base.get("nombre") or clave)
        descripcion = st.text_area("Descripción", base.get("descripcion") or "")
        st.caption("Completa datos verificables. El marcado de producto no mostrará stock, precio ni enlaces "
                   "hasta que estos se confirmen explícitamente.")
        borradores = seo_geo.borradores_editoriales({**base, "nombre": nombre,
                                                     "descripcion": descripcion}, clave)
        st.caption("Borradores desde la ficha actual — revísalos antes de publicar.\n\n"
                   f"Título: {borradores['titulo_seo']}\n\n"
                   f"Descripción: {borradores['descripcion_seo']}\n\n"
                   f"Texto alternativo: {borradores['texto_alternativo_imagen']}")
        st.caption("Resumen de partida (revísalo antes de guardar): "
                   + seo_geo.resumen_factual_sugerido({**base, "nombre": nombre,
                                                        "descripcion": descripcion}, clave))

        a, b = st.columns(2)
        marca = a.text_input("Marca", value=base.get("marca", ""), max_chars=100)
        modelo = b.text_input("Modelo o referencia", value=base.get("modelo", ""), max_chars=100)
        a, b = st.columns(2)
        gtin = a.text_input("GTIN / EAN", value=base.get("gtin", ""), max_chars=20,
                            help="Código de barras verificado en el envase o fabricante.")
        mpn = b.text_input("MPN del fabricante", value=base.get("mpn", ""), max_chars=100)
        unidad_venta = st.text_input("Unidad de venta", value=base.get("unidad_venta", ""),
                                     max_chars=100, placeholder="Ej.: unidad, resma de 500 hojas")
        compatibilidad = st.text_area("Compatibilidad verificada", value=base.get("compatibilidad", ""),
                                      max_chars=600, height=75)
        atributos_tecnicos = st.text_area(
            "Atributos técnicos (una línea por atributo: Nombre: valor)",
            value=base.get("atributos_tecnicos", ""), max_chars=2000, height=100,
            placeholder="Tamaño: A4\nGramaje: 75 g/m²\nColor: Blanco")

        st.markdown("**Contenido para buscadores**")
        seo_titulo = st.text_input("Título SEO", value=base.get("seo_titulo", ""), max_chars=90,
                                   help="Procura 50–60 caracteres e identifica producto y marca.")
        seo_descripcion = st.text_area(
            "Descripción SEO", value=base.get("seo_descripcion", ""), max_chars=250, height=80,
            help="Escribe una descripción propia, concreta y fiel a la ficha; idealmente 120–160 caracteres.")
        seo_slug = st.text_input("Slug sugerido", value=base.get("seo_slug") or seo_geo.slugify(nombre or clave),
                                 max_chars=150, help="Segmento de URL; la URL definitiva se agrega cuando la página exista.")
        seo_url_publica = st.text_input("URL pública de la ficha", value=base.get("seo_url_publica", ""),
                                        max_chars=500, placeholder="https://gioval.cl/...")
        seo_imagen_url = st.text_input("URL pública de la imagen", value=base.get("seo_imagen_url", ""),
                                       max_chars=500, placeholder="https://gioval.cl/...")
        seo_imagen_alt = st.text_input("Texto alternativo de la imagen", value=base.get("seo_imagen_alt", ""),
                                       max_chars=180,
                                       help="Describe la foto concreta para lectores de pantalla y buscadores.")

        st.markdown("**Contenido preparado para respuestas GEO**")
        geo_resumen = st.text_area(
            "Respuesta breve sobre el producto", value=base.get("geo_resumen", ""),
            max_chars=800, height=100,
            help="Responde qué es, para quién sirve y qué especificación lo distingue, solo con datos confirmados.")
        geo_faq = st.text_area(
            "Preguntas y respuestas frecuentes", value=base.get("geo_faq", ""),
            max_chars=2500, height=120,
            help="Separa cada pareja con una línea en blanco. Escribe ‘Pregunta: …’ y ‘Respuesta: …’.")
        a, b = st.columns(2)
        geo_fuente = a.text_input("Fuente de verificación", value=base.get("geo_fuente", ""),
                                  max_chars=500, placeholder="URL del fabricante, envase o ficha técnica")
        geo_verificado = b.text_input("Verificado el (AAAA-MM-DD)",
                                      value=base.get("geo_verificado", ""), max_chars=10,
                                      placeholder="2026-11-12")

        st.markdown("**Oferta pública (opcional)**")
        a, b, c = st.columns(3)
        seo_precio_publico = a.number_input(
            "Precio público CLP, IVA incluido", min_value=0,
            value=seo_geo.precio_publico_clp(base.get("seo_precio_publico")) or 0,
            step=500, help="Solo completa si confirmas que se publicará a clientes.")
        disponibilidades = list(seo_geo.DISPONIBILIDADES)
        seo_disponibilidad = b.selectbox(
            "Disponibilidad pública", disponibilidades,
            index=disponibilidades.index(base.get("seo_disponibilidad"))
            if base.get("seo_disponibilidad") in disponibilidades else 0)
        condiciones = list(seo_geo.CONDICIONES)
        seo_condicion = c.selectbox(
            "Estado del producto", condiciones,
            index=condiciones.index(base.get("seo_condicion"))
            if base.get("seo_condicion") in condiciones else 0)
        derechos = st.selectbox("Derechos de la imagen pública", ["Sin confirmar", "propia", "licenciada"],
                                key=f"derechos_publicacion_{clave}")
        aprobar = st.checkbox("Aprobar ficha para la exportación pública")
        if st.form_submit_button("Guardar producto", icon=":material/save:", type="primary"):
            if not nombre.strip():
                st.warning("Escribe un nombre para el producto.")
                return
            if gtin.strip() and not seo_geo.gtin_schema(gtin):
                st.warning("El GTIN/EAN no parece válido. Revisa sus dígitos y el dígito de control del envase.")
                return
            for etiqueta, url in (("URL pública de la ficha", seo_url_publica),
                                  ("URL pública de la imagen", seo_imagen_url)):
                if url.strip() and not seo_geo.url_publica_valida(url):
                    st.warning(f"{etiqueta}: ingresa una URL HTTPS pública y válida.")
                    return
            if geo_verificado.strip():
                try:
                    date.fromisoformat(geo_verificado.strip())
                except ValueError:
                    st.warning("La fecha de verificación debe tener formato AAAA-MM-DD.")
                    return
            publicacion = dict(base.get("publicacion") or {})
            if aprobar:
                evidencia = [
                    {"campo": campo, "referencia": geo_fuente.strip(), "verificado_el": geo_verificado.strip()}
                    for campo in ("nombre", "descripcion")
                ]
                publicacion.update(
                    estado="aprobado", nombre=nombre.strip(), descripcion=descripcion.strip(),
                    marca=marca.strip(), gtin=gtin.strip(), codigo_fabricante=mpn.strip(),
                    slug=seo_geo.slugify(seo_slug or nombre), fuentes=evidencia,
                    imagenes=[{"url": seo_imagen_url.strip(), "alt": seo_imagen_alt.strip(),
                              "aprobada": derechos != "Sin confirmar", "derechos": derechos}],
                )
                pendientes = []
                if not catalogo_publico.crear_producto_jsonld(
                    clave, {**base, "publicacion": publicacion}, "https://gioval.cl", pendientes,
                ):
                    st.warning("Para aprobar, asigna un identificador y completa nombre, descripción, fuente, "
                               "fecha de verificación, imagen HTTPS, texto alternativo y derechos de uso.")
                    return
            elif publicacion.get("estado") == "aprobado":
                publicacion["estado"] = "borrador"
            try:
                bib.actualizar(clave, version_esperada=base, nombre=nombre.strip(), descripcion=descripcion.strip(),
                               marca=marca.strip(), modelo=modelo.strip(), gtin=gtin.strip(), mpn=mpn.strip(),
                               unidad_venta=unidad_venta.strip(), compatibilidad=compatibilidad.strip(),
                               atributos_tecnicos=atributos_tecnicos.strip(), seo_titulo=seo_titulo.strip(),
                               seo_descripcion=seo_descripcion.strip(), seo_slug=seo_slug.strip(),
                               seo_url_publica=seo_url_publica.strip(), seo_imagen_url=seo_imagen_url.strip(),
                               seo_imagen_alt=seo_imagen_alt.strip(),
                               seo_precio_publico=seo_precio_publico or None,
                               seo_disponibilidad=(seo_disponibilidad if seo_disponibilidad != "No indicar" else None),
                               seo_condicion=(seo_condicion if seo_condicion != "No indicar" else None),
                               geo_resumen=geo_resumen.strip(), geo_faq=geo_faq.strip(),
                               geo_fuente=geo_fuente.strip(), geo_verificado=geo_verificado.strip(),
                               publicacion=publicacion or None)
            except ValueError as error:
                st.warning(str(error))
                st.caption("Cierra y vuelve a abrir esta sección para cargar los datos actuales.")
                return
            estado.pop(llave, None)
            estado["p_msg"] = ("ok", "Ficha del producto guardada.")
            st.rerun()
