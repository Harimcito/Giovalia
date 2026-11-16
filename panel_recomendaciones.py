"""Panel de orientación de precios para los productos de la biblioteca."""
from datetime import datetime, timezone
import math
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import streamlit as st
import miniaturas as mini

from clasificacion_comercial import clasificar
from recomendaciones import Referencias, buscar_referencias, recomendar
from recomendaciones import auditar_final
import json
from pathlib import Path


FAMILIAS = {
    "toner": "Tóner", "tinta": "Tintas", "tambor": "Tambores",
    "resmas": "Resmas", "papel": "Papeles", "perifericos": "Periféricos",
    "pilas": "Pilas", "escritura": "Escritura", "organizacion": "Organización",
    "adhesivos": "Adhesivos y sujeción", "general": "Otros productos",
    "equipos_impresion": "Impresoras y plotters", "tecnologia": "Computación y equipos",
    "oficina": "Artículos de oficina", "manualidades": "Manualidades",
}
VARIANTES = {"original": "Original", "alternativo": "Alternativo", "no_especificada": "Sin definir"}


def _pesos(valor):
    if valor is None:
        return "Pendiente"
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "Pendiente"
    if not math.isfinite(numero):
        return "Pendiente"
    return "$ " + format(numero, ",.0f").replace(",", ".")


def _fecha(valor):
    if not valor:
        return "Sin consulta externa"
    try:
        fecha = datetime.fromisoformat(str(valor))
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        return fecha.astimezone(ZoneInfo("America/Santiago")).strftime("%d/%m/%Y · %H:%M")
    except ValueError:
        return str(valor)


def _limpiar_variante(texto):
    texto = re.sub(r"\bo\s+(?:su\s+)?(?:equivalente|similar)\b", " ", str(texto or ""), flags=re.I)
    texto = re.sub(r"\b(?:original(?:es)?|oem|genuin[oa]s?|alternativ[oa]s?|compatibles?|genéric[oa]s?|generic[oa]s?|remanufacturad[oa]s?)\b", " ", texto, flags=re.I)
    return re.sub(r"\s+", " ", texto).strip(" /,;.-")


def _con_variante(ficha, variante):
    temporal = dict(ficha)
    temporal["nombre"] = f"{_limpiar_variante(ficha.get('nombre'))} {variante}".strip()
    aliases = ficha.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    temporal["aliases"] = [f"{_limpiar_variante(alias)} {variante}".strip() for alias in aliases if isinstance(alias, str)]
    return temporal


def _identificable(identidad):
    if identidad.get("familia") in {"toner", "tinta", "tambor"}:
        return bool(identidad.get("preciso") and identidad.get("modelo")
                    and identidad.get("variante") in {"original", "alternativo"})
    if identidad.get("familia") == "resmas":
        consulta = str(identidad.get("consulta") or "").upper()
        formato = re.search(r"\b(?:CARTA|OFICIO|A4)\b", consulta)
        gramaje = re.search(r"\b\d{2,3}\s*(?:GR(?:AMOS?|S)?|G(?:/M2)?)\b", consulta)
        hojas = re.search(r"\b\d{2,5}\s*HOJAS\b", str(identidad.get("presentacion") or "").upper())
        return bool(identidad.get("preciso") or (formato and gramaje and hojas))
    return bool(identidad.get("preciso"))


def _consulta_guardada(almacen, clave, ficha):
    try:
        return almacen.leer(clave, ficha)
    except (OSError, ValueError):
        return None


def _consultar(almacen, clave, ficha):
    with st.status("Consultando fuentes de precios…", expanded=True) as estado:
        st.caption("Se buscan productos equivalentes por modelo, tipo y presentación.")
        try:
            datos = buscar_referencias(clave, ficha)
            consulta = almacen.guardar(clave, ficha, datos)
        except Exception:
            estado.update(label="La consulta no pudo completarse", state="error", expanded=False)
            st.warning("No pude actualizar las referencias. Puedes reintentar; los precios anteriores se conservan.")
            return _consulta_guardada(almacen, clave, ficha)
        cantidad = len((consulta or {}).get("referencias") or [])
        etiqueta = f"Consulta terminada · {cantidad} comparable(s) verificado(s)" if cantidad else "Consulta terminada · sin comparables verificados"
        estado.update(label=etiqueta, state="complete", expanded=False)
        return consulta


def _mostrar_fuentes(consulta):
    if not consulta:
        return
    refs = consulta.get("referencias") or []
    avisos = consulta.get("avisos") or []
    descartadas = consulta.get("descartadas") or []
    with st.expander("Fuentes, coincidencias y descartes", icon=":material/fact_check:"):
        st.caption("Última consulta: " + _fecha(consulta.get("fecha")))
        if not consulta.get("vigente", False):
            st.warning("La consulta guardada está vencida y no se usa para recomendar un precio de mercado.")
        if refs:
            filas = []
            for ref in refs:
                enlace = str(ref.get("url") or "")
                if urlparse(enlace).scheme != "https":
                    enlace = ""
                filas.append({
                    "Fuente": ref.get("fuente", ""), "Producto comparable": ref.get("nombre", ""),
                    "Precio neto": _pesos(ref.get("precio_neto")),
                    "Precio con IVA": _pesos(ref.get("precio_con_iva")), "Consultar": enlace,
                })
            st.dataframe(filas, hide_index=True, width="stretch",
                         column_config={"Consultar": st.column_config.LinkColumn(display_text="Ver producto")},
                         alt="Fuentes y productos usados para la referencia de precio")
        else:
            st.caption("Esta consulta no encontró productos con identidad y precio suficientemente verificables.")
        for aviso in avisos:
            st.caption("• " + str(aviso))
        if descartadas:
            st.caption(f"Se descartaron {len(descartadas)} resultados por diferencias o datos insuficientes.")
            st.dataframe([{"Producto": ref.get("nombre", ""), "Fuente": ref.get("fuente", ""),
                           "Motivo": ref.get("motivo", "")} for ref in descartadas],
                         hide_index=True, width="stretch", alt="Productos descartados y motivo de la exclusión")


def precios_finales_automaticos(bib):
    st.subheader("Precios finales sugeridos", icon=":material/auto_graph:")
    archivo = Path(bib.dir) / "criterio_precios.json"
    try:
        preferencias = json.loads(archivo.read_text("utf-8"))
    except (OSError, ValueError):
        preferencias = {}
    st.caption("Con costo conocido: (costo neto + flete unitario + utilidad) × 1,19. Sin costo: referencia de mercado o venta anterior + flete adicional, con IVA; utilidad pendiente. Los precios de venta anteriores ya incluyen utilidad y no reciben otro recargo.")
    confirmado = False
    a, b = st.columns(2)
    flete = a.number_input("Flete pagado por unidad ($)", min_value=0.0, value=preferencias.get("flete"), step=100.0, key="auto_flete")
    recargo = b.number_input("Utilidad objetivo: recargo sobre base y flete (%)", min_value=0.0, value=float(preferencias.get("recargo", 20)), step=1.0, key="auto_recargo")
    st.caption("El 20 % inicial es un supuesto editable de simulación, no una tasa de utilidad demostrada por rubro. Flete unitario = parte del flete del lote ÷ unidades. No repitas el flete total en cada unidad.")
    if flete is None:
        st.warning("Flete pendiente: los importes se calculan provisionalmente con $0 de flete. Completa el monto para obtener un precio con ese gasto incluido.")
    st.caption("El flete global es adicional a la referencia de venta. Usa $0 si ya estaba incluido o el producto no tiene flete. Puedes reemplazarlo por producto abajo.")
    ajustes = preferencias.get("productos", {})
    with st.expander("Costos y fletes por producto", icon=":material/local_shipping:"):
        st.caption("Costo vacío = pendiente. Flete vacío = usar global; $0 = sin flete. Guarda para recalcular. Los precios anteriores se conservan.")
        filas_edicion = [{"Código": k, "Producto": f.get("nombre") or k,
                          "Costo proveedor": ajustes.get(k, {}).get("costo", f.get("costo_neto")),
                          "Flete unitario": ajustes.get(k, {}).get("flete")} for k, f in bib.indice.items()]
        editado = editor_fotos(filas_edicion, bib, disabled=["Código", "Producto"], hide_index=True, width="stretch", height=300, key="auto_costos",
            column_config={c: st.column_config.NumberColumn(min_value=0, format="$ %.0f") for c in ("Costo proveedor", "Flete unitario")}, alt="Costos reales y fletes individuales editables")
    if st.button("Guardar criterio para todo el catálogo", key="auto_guardar"):
        temporal = archivo.with_suffix(".tmp")
        ajustes = {r["Código"]: {"costo": r["Costo proveedor"], "flete": r["Flete unitario"]} for r in editado}
        temporal.write_text(json.dumps({"flete": flete, "recargo": recargo, "productos": ajustes}), encoding="utf-8")
        temporal.replace(archivo)
        st.success("Criterio guardado. Se aplicará automáticamente al abrir el catálogo.")
    almacen = Referencias(bib.dir)
    filas, calculos = [], {}
    for clave, ficha in bib.indice.items():
        consulta = _consulta_guardada(almacen, clave, ficha)
        referencia = recomendar(clave, ficha, consulta)
        mercado = referencia.get("precio_neto") if referencia.get("origen") == "Comparables externos" else None
        ajuste = ajustes.get(clave, {})
        ficha_calculo = dict(ficha)
        if ajuste.get("costo") is not None:
            ficha_calculo["costo_neto"] = ajuste["costo"]
        flete_producto = ajuste.get("flete") if ajuste.get("flete") is not None else flete
        r = auditar_final(ficha_calculo, mercado, flete_producto or 0, recargo, confirmado)
        r["flete_completo"] = flete_producto is not None
        calculos[clave] = r
        filas.append({"Producto": ficha.get("nombre") or clave, "Final sugerido": r.get("final"),
                      "Auditoría": r["estado"], "Base utilizada": r.get("base_tipo", "Sin base"),
                      "Base neta": r.get("base"), "Flete unitario": flete_producto,
                      "Utilidad estimada": r.get("utilidad"), "IVA venta": r.get("iva"),
                      "Referencia mercado con IVA": r.get("mercado")})
    tabla_fotos(filas, bib, hide_index=True, width="stretch", height=360,
                 column_config={"Producto": st.column_config.TextColumn(width="medium"),
                 **{c: st.column_config.NumberColumn(format="$ %.0f") for c in ("Final sugerido", "Base neta", "Flete unitario", "Utilidad estimada", "IVA venta", "Referencia mercado con IVA")}},
                 alt="Precio final automático con IVA, flete, utilidad y auditoría de mercado")
    st.caption("Si cubrir la utilidad objetivo supera el comparable, se indica «Sobre referencia de mercado»: revisa el costo o el objetivo antes de ofertar. La utilidad estimada descuenta proveedor y flete; faltan otros gastos e impuesto a la renta. Sin comparable, el cálculo es por fórmula y no una validación de competitividad.")
    clave = st.selectbox("Producto cuyo precio final deseas usar", list(calculos), index=None, format_func=lambda k: bib.indice[k].get("nombre") or k, key="auto_producto")
    if clave:
        r = calculos[clave]
        st.metric("Precio final sugerido con IVA", _pesos(r.get("final")))
        habilitado = r.get("final") is not None and r["flete_completo"]
        if st.button("Usar este precio final en nuevas cotizaciones", disabled=not habilitado, key="auto_adoptar", type="primary"):
            bib.actualizar(clave, precio_recomendado_neto=r["neto"], precio_recomendado_fuente=r["estado"] + " · " + r.get("base_tipo", ""), precio_recomendado_fecha=datetime.now(timezone.utc).isoformat())
            st.success("Precio final guardado para nuevas cotizaciones.")
        if not habilitado:
            st.caption("Para adoptar el precio, define el flete aplicable; ingresa $0 si no hay. Sin costo de proveedor, la utilidad sigue pendiente.")


def panel(bib):
    """Consulta bajo selección, clasifica todo el catálogo y aplica solo por decisión explícita."""
    with st.container(border=True, key="gv_finales_automaticos"):
        precios_finales_automaticos(bib)
    with st.container(border=True, key="gv_recomendaciones"):
        st.subheader("Orientación de precios por producto", icon=":material/price_check:")
        st.caption("Consulta referencias de precio y margen por producto.")
        indice = {str(clave): ficha for clave, ficha in (bib.indice or {}).items() if isinstance(ficha, dict)}
        if not indice:
            st.info("Agrega productos a la biblioteca para ver su clasificación y sus referencias de precio.")
            return
        almacen = Referencias(bib.dir)
        categorias = {clave: clasificar(clave, ficha) for clave, ficha in indice.items()}
        opciones_familia = sorted({c.get("familia", "general") for c in categorias.values()},
                                  key=lambda f: FAMILIAS.get(f, f).casefold())
        a, b = st.columns([1, 1])
        with a:
            modo_ui = st.selectbox("Criterio de comparación", ["Venta habitual", "Mercado Público"], key="rec_modo")
        with b:
            familia = st.selectbox("Familia de productos", [""] + opciones_familia,
                                    format_func=lambda f: "Todas las familias" if not f else FAMILIAS.get(f, f),
                                    key="rec_familia")
        modo = "mercado_publico" if modo_ui == "Mercado Público" else "habitual"
        if modo == "mercado_publico":
            st.caption("Este criterio usa el percentil 25 de precios minoristas comparables por comercio cuando hay varias fuentes; no son precios adjudicados por el Estado ni una predicción del precio ganador.")
        claves = sorted([k for k in indice if not familia or categorias[k].get("familia") == familia],
                        key=lambda k: str(indice[k].get("nombre") or k).casefold())
        if st.session_state.get("rec_producto") not in claves:
            st.session_state["rec_producto"] = None
        elegido = st.selectbox("Producto para consultar comparables", claves, index=None,
                               placeholder="Selecciona un producto para revisar sus referencias",
                               format_func=lambda k: f"{k} · {indice[k].get('nombre') or k}", key="rec_producto")
        automatico = st.checkbox("Consultar fuentes al seleccionar un producto identificable", value=True, key="rec_automatico")
        st.caption("La tabla usa las referencias guardadas. La consulta externa comienza al seleccionar un producto; no se consultan cientos de productos a la vez.")

        consulta = resultado = None
        ficha_comparacion = None
        identificable = False
        if elegido in indice:
            ficha_comparacion = dict(indice[elegido])
            identidad = categorias[elegido]
            necesita_variante = identidad.get("familia") in {"toner", "tinta", "tambor"} and identidad.get("variante") not in {"original", "alternativo"}
            if necesita_variante:
                opcion = st.selectbox("Comparar como", ["No definida", "Original", "Alternativo"],
                                       key="rec_variante_" + elegido)
                if opcion != "No definida":
                    ficha_comparacion = _con_variante(ficha_comparacion, opcion.lower())
                else:
                    st.info("Define si compararás un original o un alternativo. «O equivalente» no permite validar un precio comparable.")
            identidad = clasificar(elegido, ficha_comparacion)
            identificable = _identificable(identidad)
            consulta = _consulta_guardada(almacen, elegido, ficha_comparacion)
            actualizar = st.button("Actualizar comparables del producto", icon=":material/refresh:",
                                     key="rec_actualizar", disabled=not identificable)
            if identificable and (actualizar or (automatico and (not consulta or not consulta.get("vigente", False)))):
                consulta = _consultar(almacen, elegido, ficha_comparacion)
            elif not identificable:
                st.caption("Completa el modelo, el tipo o la presentación para consultar precios comparables.")
            resultado = recomendar(elegido, ficha_comparacion, consulta, modo)

        filas = []
        resultados = {}
        for clave in claves:
            if clave == elegido and resultado is not None:
                r = resultado
            else:
                r = recomendar(clave, indice[clave], _consulta_guardada(almacen, clave, indice[clave]), modo)
            resultados[clave] = r
            filas.append({
                "Código": clave, "Producto": indice[clave].get("nombre") or clave,
                "Familia": FAMILIAS.get(r.get("familia"), r.get("familia") or "Otros productos"),
                "Rubro": r.get("rubro", ""), "Modelo": r.get("modelo") or "Por precisar",
                "Presentación": r.get("presentacion") or "Por precisar",
                "Referencia neta": _pesos(r.get("precio_neto")),
                "Margen antes de gastos": "Costo pendiente" if r.get("margen_pct") is None else f"{r['margen_pct']:.1f} %",
            })
        con_externas = sum(r.get("origen") == "Comparables externos" for r in resultados.values())
        st.caption(f"{len(filas)} productos en esta vista · {con_externas} con comparables externos verificados.")
        tabla_fotos(filas, bib, hide_index=True, width="stretch", height=340,
                     column_order=["Producto", "Familia", "Referencia neta", "Margen antes de gastos",
                                   "Modelo", "Presentación", "Rubro", "Código"],
                     alt="Clasificación y orientación de precio de los productos de la biblioteca")
        if resultado is None:
            return

        st.markdown("**Referencia del producto seleccionado**")
        a, b, c = st.columns(3)
        a.metric("Referencia neta", _pesos(resultado.get("precio_neto")))
        b.metric("Referencia con IVA", _pesos(resultado.get("precio_con_iva")))
        c.metric("Margen antes de otros gastos", "Costo pendiente" if resultado.get("margen_pct") is None else f"{resultado['margen_pct']:.1f} %")
        if resultado.get("advertencia"):
            st.warning(str(resultado["advertencia"]))
        if resultado.get("margen_pct") is None:
            st.caption("El precio previo es de venta o de una cotización anterior, no el costo del proveedor. Registra el costo para calcular margen; añade transporte y otros gastos en el simulador para evaluar rentabilidad.")
        else:
            st.caption("Este margen considera solo el costo del proveedor. Faltan transporte, otros gastos e impuesto a la renta para estimar la utilidad final.")
        _mostrar_fuentes(consulta)
        precio = resultado.get("precio_neto")
        puede_guardar = precio is not None and math.isfinite(float(precio)) and float(precio) > 0
        if st.button("Usar como sugerencia para nuevas cotizaciones", type="primary", icon=":material/check:",
                      disabled=not puede_guardar, key="rec_usar_sugerencia"):
            fecha = resultado.get("fecha") or datetime.now(timezone.utc).isoformat()
            try:
                procedencia = resultado["origen"] + " · " + VARIANTES.get(resultado.get("variante"), "Sin definir")
                bib.actualizar(elegido, precio_recomendado_neto=float(precio),
                                precio_recomendado_fuente=procedencia, precio_recomendado_fecha=fecha)
            except Exception:
                st.error("No pude guardar la sugerencia. Reintenta antes de usarla en una cotización.")
            else:
                st.success("Sugerencia guardada para nuevas cotizaciones. El historial conserva sus precios.")
                st.toast("Sugerencia de precio guardada", icon=":material/check_circle:")
        st.caption("Guardar esta sugerencia no cambia cotizaciones existentes ni modifica automáticamente los precios al navegar por el catálogo.")
        if indice[elegido].get("precio_recomendado_neto") is not None:
            if st.button("Quitar sugerencia guardada", key="rec_quitar_sugerencia", icon=":material/undo:"):
                bib.actualizar(elegido, precio_recomendado_neto=None,
                                precio_recomendado_fuente=None, precio_recomendado_fecha=None)
                st.success("Se restauró el criterio anterior para nuevas cotizaciones.")


def tabla_fotos(datos, bib, **kw):
    df, opciones = mini.preparar(datos, bib, kw)
    return st.dataframe(df, **opciones)

def editor_fotos(datos, bib, **kw):
    df, opciones = mini.preparar(datos, bib, kw)
    editado = st.data_editor(df, **opciones)
    return editado.drop(columns=[mini.COLUMNA], errors="ignore").to_dict("records")
