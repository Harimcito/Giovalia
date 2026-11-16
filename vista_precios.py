"""Simulador integrado: lee catálogo; no modifica precios ni cotizaciones."""
import csv
import io
from datetime import date

import streamlit as st
import costos_fijos_ui
from miniaturas import miniatura
import identidad_visual as visual

from precios import costo_efectivo, evaluar_precio, precio_para_margen, sumar_costos_fijos
from revisor import clp


def pesos(valor):
    return clp(valor)


def _recordar(nombre):
    estado = st.session_state
    borrador = estado.setdefault("_precios_borrador", {})
    borrador[nombre] = estado["pr_" + nombre]
    if nombre in {"costo", "unidades", "transporte", "otros", "recuperable", "producto", "costos_fijos"}:
        estado["pr_completos"] = borrador["completos"] = False
    if nombre in {"producto", "referencia", "base_ref", "fuente", "fecha"}:
        estado["pr_comparable"] = borrador["comparable"] = False
    if nombre == "producto":
        for campo in ("costo", "precio"):
            estado["pr_" + campo] = borrador[campo] = None


def _opciones(nombre, defecto):
    """Separar borrador de claves de widgets evita perderlo al cambiar de sección."""
    estado = st.session_state
    clave = "pr_" + nombre
    if clave not in estado:
        estado[clave] = estado.get("_precios_borrador", {}).get(nombre, defecto)
    return {"key": clave, "on_change": _recordar, "args": (nombre,)}


def _simulador(bib, plan):
    visual.titulo("Precios y rentabilidad", "Prueba un precio. Conoce tu margen. Decide con tus costos a la vista.", "Inteligencia comercial", "%")
    izquierda, derecha = st.columns([1, 1.15], gap="large")
    with izquierda.container(border=True, key="gv_cost_inputs"):
        st.badge("01 · Tu costo", color="blue", icon=":material/inventory_2:")
        indice = bib.indice
        seleccionado = st.session_state.get("pr_producto", st.session_state.get("_precios_borrador", {}).get("producto", ""))
        if seleccionado and seleccionado not in indice:
            st.session_state["pr_producto"] = ""
            _recordar("producto")
        producto = st.selectbox(
            "Producto de la biblioteca (opcional)", [""] + sorted(indice),
            format_func=lambda k: "Ingresar un producto manualmente" if not k else
            f"{k} · {indice[k].get('nombre', k)}",
            **_opciones("producto", ""),
        )
        ficha = indice.get(producto, {})
        imagen_producto = miniatura(bib, producto)
        if imagen_producto:
            st.image(imagen_producto, width=80, alt="Foto de referencia del producto seleccionado")
        if producto and st.button("Cargar costo y último precio del catálogo", key="pr_cargar"):
            for nombre, campo in (("costo", "costo_neto"), ("precio", "precio_neto")):
                valor = ficha.get(campo)
                st.session_state["pr_" + nombre] = float(valor) if valor is not None else None
                _recordar(nombre)
            st.session_state["pr_base"] = "Con IVA"
            # El precio del catálogo está guardado neto; se convierte solo al cargarlo.
            if ficha.get("precio_neto") is not None:
                st.session_state["pr_precio"] = float(ficha["precio_neto"]) * 1.19
                _recordar("precio")
            _recordar("base")
            if ficha.get("costo_neto") is None:
                st.warning("Este producto no tiene costo del proveedor registrado; ingrésalo abajo.")
        if producto:
            st.caption("Al cargar se reemplazan solo el costo y el precio de esta simulación.")

        st.subheader("La base de tu precio")
        a, b = st.columns(2)
        with a:
            costo = st.number_input("Costo del proveedor por unidad, sin IVA ($)", min_value=0.0,
                                    value=None, step=100.0, **_opciones("costo", None))
            unidades = st.number_input("Unidades del lote", min_value=1,
                                       step=1, **_opciones("unidades", 1))
            recuperable = st.checkbox("El IVA del producto da derecho a crédito fiscal", **_opciones("recuperable", True))
        with b:
            transporte = st.number_input("Flete de este lote: monto pagado ($)",
                                         min_value=0.0, value=None, step=100.0, **_opciones("transporte", None))
            otros = st.number_input("Otros costos del lote ($)", min_value=0.0,
                                    step=100.0, **_opciones("otros", 0.0))
            st.caption("Incluye embalaje y otros gastos propios de este pedido.")
        repartir_fijos = False
        if plan:
            repartir_fijos = st.checkbox("Incluir costos fijos del local", **_opciones("costos_fijos", False))
            if repartir_fijos:
                st.caption(f"Reparto mensual · {costos_fijos_ui.resumen(plan)}")
        with st.expander("Cómo distribuir el transporte", icon=":material/local_shipping:"):
            st.caption(
            "Si un flete trae varios productos, asigna a cada lote solo su parte del flete. "
            "Usa unidades para productos similares o peso/volumen para productos distintos. "
            "No repitas el flete completo en cada producto."
        )
        completos = st.checkbox("Revisé todos los gastos, incluida cualquier entrega al cliente",
                                **_opciones("completos", False))
    with derecha.container(border=True, key="gv_price_result"):
        st.badge("02 · Tu resultado", color="violet", icon=":material/query_stats:")
        if costo is None or transporte is None:
            st.info("Ingresa el costo y el transporte. Si el flete es gratuito, ingresa 0 explícitamente.")
            return
        try:
            costos = costo_efectivo(costo, unidades, transporte, otros, recuperable)
            if repartir_fijos:
                costos = sumar_costos_fijos(costos, plan)
        except ValueError as exc:
            st.error(str(exc))
            return
        if not completos:
            st.warning("Cálculo parcial: los resultados solo descuentan los gastos ingresados. Confirma los gastos antes de decidir una oferta.")
        a, b = st.columns(2)
        a.metric("Costo efectivo por unidad", pesos(costos["costo_unitario"]))
        b.metric("Costo efectivo del lote", pesos(costos["costo_total"]))
        if repartir_fijos:
            st.caption(f"Incluye {pesos(costos['fijo_unitario'])} de gasto fijo por unidad.")
        st.caption(f"Dinero inicial necesario: **{pesos(costos['desembolso_inicial'])}**")
        st.caption("El dinero inicial incluye el IVA que pagas al proveedor, aunque después puedas usarlo como crédito fiscal.")

        st.subheader("¿Qué deja esta venta?")
        a, b = st.columns(2)
        with a:
            precio = st.number_input("Precio de venta por unidad ($)", min_value=0.0, value=None,
                                     step=100.0, **_opciones("precio", None))
        with b:
            base = st.selectbox("El precio ingresado es", ["Con IVA", "Neto, sin IVA"], **_opciones("base", "Con IVA"))
        resultado = None
        if precio is not None:
            try:
                resultado = evaluar_precio(costos, precio, base == "Con IVA")
            except ValueError as exc:
                st.error(str(exc))
                return
            a, b = st.columns(2)
            a.metric("Resultado por unidad antes de renta", pesos(resultado["resultado_unitario"]))
            st.caption(f"Resultado del lote antes de renta: **{pesos(resultado['resultado_pedido'])}**")
            b.metric("Margen sobre venta neta", "Sin venta" if resultado["margen_pct"] is None else f"{resultado['margen_pct']:.2f} %")
            if resultado["resultado_pedido"] < 0:
                st.error("Este precio genera una pérdida incluso con los gastos actualmente ingresados.")
            elif completos:
                st.success("El precio cubre los costos ingresados. Revisa también las condiciones de la compra.")
            st.caption(f"Precio sin IVA: {pesos(resultado['precio_neto'])} · Precio final por unidad: {pesos(resultado['precio_con_iva'])}")
        st.caption("Margen = (venta sin IVA − costo efectivo) ÷ venta sin IVA. Un recargo del 30 % al costo produce un margen del 23,08 %.")

    escenarios_tab, mercado_tab = st.tabs(["Escenarios de precio", "Referencia de Mercado Público"])
    with escenarios_tab:
        st.subheader("Encuentra espacio para tu margen")
        st.caption("Los márgenes de 10 %, 20 % y 30 % son escenarios de comparación, no recomendaciones para tu rubro.")
        objetivo = st.number_input("Otro margen para comparar (%)", min_value=0.0, max_value=99.0,
                                  step=1.0, **_opciones("objetivo", 15.0))
        escenarios = []
        for margen in sorted({0.0, 10.0, 20.0, 30.0, float(objetivo)}):
            r = precio_para_margen(costos, margen)
            escenarios.append({
                "Margen buscado (%)": margen,
                "Precio neto unitario ($)": float(r["precio_neto"]),
                "Precio final unitario ($)": float(r["precio_con_iva"]),
                "Resultado por unidad ($)": float(r["resultado_unitario"]),
                "Resultado del lote ($)": float(r["resultado_pedido"]),
            })
        mostrar = [{k: (f"{v:g} %" if k == "Margen buscado (%)" else pesos(v)) for k, v in e.items()} for e in escenarios]
        st.dataframe(mostrar, hide_index=True, width="stretch", alt="Precios y resultados para distintos márgenes")
        import pandas as pd
        st.bar_chart(pd.DataFrame({"Margen buscado": [f"{e['Margen buscado (%)']:g} %" for e in escenarios],
                                   "Resultado por unidad ($)": [e["Resultado por unidad ($)"] for e in escenarios]}),
                     x="Margen buscado", y="Resultado por unidad ($)", height=200, color="#3153E8",
                     alt="Resultado por unidad para cada margen y precio de venta")
        piso = precio_para_margen(costos, 0)["precio_con_iva"]
        st.caption(f"Punto de equilibrio con los gastos ingresados: {pesos(piso)} por unidad, IVA incluido. A ese precio prácticamente no queda ganancia.")

    with mercado_tab:
        st.subheader("Compite con una referencia verificada")
        st.caption(
            "Compara el mismo producto, marca/modelo, presentación, cantidad, fecha y condiciones de entrega. "
            "Una adjudicación histórica no garantiza el precio ganador de una nueva compra."
        )
        referencia = st.number_input("Precio de referencia por unidad ($), opcional", min_value=0.0, value=None,
                                     step=100.0, **_opciones("referencia", None))
        base_ref = st.selectbox("El precio de referencia es", ["Con IVA", "Neto, sin IVA"], **_opciones("base_ref", "Con IVA"))
        fuente = st.text_input("Fuente o código de la compra de referencia", **_opciones("fuente", ""))
        fecha = st.date_input("Fecha de la referencia", **_opciones("fecha", date.today()))
        comparable = st.checkbox("Verifiqué que el producto, la unidad y las condiciones son comparables", **_opciones("comparable", False))
        if referencia is not None and referencia > 0:
            if not comparable or not fuente.strip():
                st.info("Registra la fuente y confirma la comparación para evaluar esa referencia.")
            else:
                try:
                    ref = evaluar_precio(costos, referencia, base_ref == "Con IVA")
                except ValueError as exc:
                    st.error(str(exc))
                    return
                if fecha > date.today():
                    st.warning("La referencia tiene una fecha futura; corrígela antes de usarla para decidir.")
                elif ref["resultado_pedido"] <= 0:
                    st.error("Igualar o bajar esta referencia no deja ganancia con tus costos. Revisa proveedor, flete o conveniencia de participar.")
                else:
                    st.info(f"Igualar la referencia dejaría {pesos(ref['resultado_unitario'])} por unidad y un margen del {ref['margen_pct']:.2f} %, antes de renta y con los gastos ingresados.")
                    alternativas = [e for e in escenarios if e["Margen buscado (%)"] > 0 and e["Precio final unitario ($)"] <= float(ref["precio_con_iva"])]
                    if completos and alternativas:
                        candidato = max(alternativas, key=lambda e: e["Margen buscado (%)"])
                        st.success(f"Alternativa para revisar: {pesos(candidato['Precio final unitario ($)'])} por unidad con IVA, para un margen de al menos {candidato['Margen buscado (%)']:g} %. Es el mayor margen entre los escenarios mostrados que no supera tu referencia; no predice adjudicación ni es un óptimo de mercado.")
                    elif completos:
                        st.warning("Ningún escenario de margen positivo mostrado cabe bajo la referencia. Puedes comparar un margen menor, pero revisa si el esfuerzo y el riesgo lo justifican.")
        st.caption("Revisa las bases o términos de cada compra: precio, entrega y requisitos técnicos pueden afectar la conveniencia de ofertar. Si debes entregar al comprador, incorpora ese costo.")
        st.markdown("[Consultar datos oficiales de ChileCompra](https://www.chilecompra.cl/datos-abiertos/)")

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Dato", "Valor"])
    for nombre, valor in {
        "Producto": producto or "Manual", "Unidades": unidades,
        "Costo proveedor neto unitario": costo, "Transporte asignado al lote": transporte,
        "Otros costos efectivos": otros, "IVA producto recuperable": recuperable,
        "Gastos confirmados": completos, **costos,
        **(resultado or {}), "Referencia ingresada": referencia, "Base referencia": base_ref,
        "Fuente referencia": fuente, "Fecha referencia": fecha,
        "Referencia comparable confirmada": comparable,
        "Alcance": "Estimado antes de impuesto a la renta; descuenta solo los gastos ingresados",
    }.items():
        texto = str(valor) if valor is not None else ""
        # Evitar fórmulas al abrir texto libre en Excel.
        if texto.startswith(("=", "+", "-", "@")) and isinstance(valor, str):
            texto = "'" + texto
        writer.writerow([nombre, texto])
    writer.writerow([])
    writer.writerow(escenarios[0].keys())
    for escenario in escenarios:
        writer.writerow(escenario.values())
    st.download_button("Descargar este análisis", data=buffer.getvalue().encode("utf-8-sig"),
                       file_name="giovalia-precios.csv", mime="text/csv", key="pr_descargar")



def vista_precios(bib, hist, *, volver=None):
    from panel_recomendaciones import panel
    seccion = st.pills("Sección de precios", ["Recomendaciones", "Simular rentabilidad", "Costos fijos"],
                       default="Recomendaciones" if "precios_seccion" not in st.session_state else None,
                       key="precios_seccion", required=True, label_visibility="collapsed", persist_state="session")
    if seccion == "Recomendaciones":
        visual.titulo("Precios recomendados", "Tus productos, su rubro y sus referencias de precio, sin empezar desde cero.", "Inteligencia comercial", "%")
        panel(bib)
    elif seccion == "Simular rentabilidad":
        _simulador(bib, costos_fijos_ui.plan_guardado(hist))
    else:
        costos_fijos_ui.presupuesto(hist, volver=volver)
