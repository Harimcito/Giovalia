"""Presupuesto mensual y simulación de precios para una cotización."""
import hashlib

import streamlit as st

import cotizacion_datos as datos
from historial import ConflictoActualizacion
import identidad_visual as visual
import listados
from precios import CLAVE_COSTOS_FIJOS, IVA, CostosFijos, numero, precio_para_margen
from revisor import clp


def plan_guardado(hist):
    return CostosFijos.leer(hist.config(CLAVE_COSTOS_FIJOS))


def resumen(plan):
    if plan.por_costo:
        tasa = f"{plan.tasa * 100:.2f}".replace(".", ",")
        return f"{tasa} % del costo directo de cada producto"
    return f"{clp(plan.tasa)} por unidad vendida"


def presupuesto(hist, *, volver=None):
    if volver:
        st.button("Volver a la cotización", icon=":material/arrow_back:", on_click=volver,
                  key="cf_volver_cotizacion")
    visual.titulo("Costos fijos del local", "Reparte arriendo, sueldos y servicios entre lo que esperas vender en el mes.",
                  "Rentabilidad", "%")
    actual = hist.config(CLAVE_COSTOS_FIJOS)
    estado = st.session_state
    base_guardada = estado.setdefault("_cf_base_guardada", actual)
    plan = CostosFijos.leer(base_guardada)
    clave = hashlib.sha256((base_guardada or "").encode()).hexdigest()[:12]
    compartido_actualizado = base_guardada != actual
    if compartido_actualizado:
        st.warning("Otra sesión cambió el presupuesto. Recarga el monto guardado para seguir editando.")
        if st.button("Recargar presupuesto", key="cf_recargar"):
            estado["_cf_base_guardada"] = actual
            st.rerun()
    if plan:
        a, b = st.columns(2)
        a.metric("Gasto mensual guardado", clp(plan.monto), border=True)
        with b.container(border=True):
            st.caption("Reparto")
            st.write(resumen(plan))
            st.caption("Monto estimado" if plan.estimado else "Monto confirmado")
    with st.container(border=True):
        st.subheader("Tu presupuesto mensual", anchor=False)
        a, b = st.columns(2)
        monto = a.number_input("Gasto fijo del mes ($)", min_value=0, value=int(plan.monto) if plan else None,
                               step=1000, key=f"cf_monto_{clave}",
                               help="Suma arriendo, servicios, sueldos fijos y otros gastos que pagas aunque no vendas.")
        tipo = b.radio("Tipo de monto", ["Estimado", "Confirmado"], horizontal=True, key=f"cf_tipo_{clave}",
                       index=0 if not plan or plan.estimado else 1)
        metodo = st.radio("Cómo repartirlo", ["Por unidad vendida", "Según el costo de cada producto"],
                          index=int(bool(plan and plan.por_costo)), horizontal=True, key=f"cf_metodo_{clave}")
        proporcional = metodo == "Según el costo de cada producto"
        coincide = plan and plan.por_costo == proporcional
        if proporcional:
            base = st.number_input("Costo directo total de lo que esperas vender en el mes ($)", min_value=1,
                                   value=int(plan.base) if coincide else None, step=1000, key=f"cf_costo_{clave}")
            st.caption("Los productos de mayor costo reciben una parte mayor. Usa la misma base de costo directo en todos.")
        else:
            base = st.number_input("Unidades que esperas vender en el mes", min_value=1,
                                   value=int(plan.base) if coincide else None, step=1, key=f"cf_unidades_{clave}")
            st.caption("Cada unidad recibe la misma parte. Funciona bien si tus productos son parecidos.")
        calculado = CostosFijos.crear(monto, base, proporcional, tipo == "Estimado") if monto is not None and base else None
        if calculado:
            st.info(f"Con estas cifras, debes recuperar **{resumen(calculado)}**.", icon=":material/calculate:")
        if st.button("Guardar presupuesto", icon=":material/save:", type="primary", key="cf_guardar",
                     disabled=calculado is None or calculado == plan or compartido_actualizado):
            valor = calculado.guardar()
            try:
                hist.set_config(CLAVE_COSTOS_FIJOS, valor, anterior=base_guardada)
            except ConflictoActualizacion:
                st.rerun()
            estado["_cf_base_guardada"] = valor
            estado["_cf_guardado"] = True
            st.rerun()
        if estado.pop("_cf_guardado", False):
            st.success("Presupuesto guardado. Ya puedes usarlo en tus cotizaciones y en el simulador.")


def precios_cotizacion(hist, bib, lineas, con_iva, *, cot_id, configurar, aplicar, bloqueado):
    panel = st.expander("Calcular precios con costos fijos", icon=":material/calculate:",
                        expanded=st.session_state.pop("_cf_abrir_al_volver", False),
                        key=f"cf_panel_cotizacion_{cot_id}", on_change="rerun")
    if not panel.open:
        return
    with panel:
        plan = plan_guardado(hist)
        if not plan:
            st.info("Guarda el gasto fijo y una previsión mensual para calcular qué parte le corresponde a cada producto.")
            st.button("Configurar costos fijos", on_click=configurar, key="cf_configurar")
            return
        st.caption(f"{clp(plan.monto)} al mes · {'Estimado' if plan.estimado else 'Confirmado'} · {resumen(plan)}")
        if not lineas:
            st.caption("Agrega un producto para calcular su precio.")
            return
        margen = st.number_input("Margen deseado sobre la venta neta (%)", min_value=0.0, max_value=99.0,
                                 value=20.0, step=1.0, key=f"cf_margen_{cot_id}")
        st.caption("Por ejemplo, con 20 % de margen, de cada $100 netos de venta quedan $20 después de los costos ingresados.")
        filas, sugeridos = [], {}
        importe_fijo = 0
        esperadas = 0
        for i, linea in enumerate(lineas):
            if not datos.esta_marcado(linea.get("incluir", linea.get("es_producto", True)), True):
                continue
            if not linea.get("es_producto", True):
                continue
            esperadas += 1
            nombre = linea.get("descripcion") or f"Producto {i + 1}"
            codigo = datos.texto_seguro(linea.get("clave_auto")).upper()
            ficha = bib.indice.get(bib.clave_vigente(codigo), {})
            try:
                costo = numero(ficha.get("costo_neto"), "Costo del producto")
            except ValueError:
                identidad = hashlib.sha256(f"{codigo}|{nombre}|{i}".encode()).hexdigest()[:10]
                importe = st.number_input(f"Costo directo sin IVA · {nombre} (línea {i + 1})",
                                          min_value=0, value=None, step=100, key=f"cf_costo_{cot_id}_{identidad}")
                if importe is None:
                    continue
                costo = numero(importe)
            fijo = plan.por_unidad(costo)
            cantidad = numero(linea.get("cantidad") or 0, "Cantidad")
            if cantidad == 0:
                continue
            resultado = precio_para_margen({"costo_unitario": costo + fijo, "unidades": cantidad,
                                            "costo_total": (costo + fijo) * cantidad}, margen,
                                           redondear_con_iva=con_iva)
            sugeridos[i] = float(resultado["precio_neto"])
            importe_fijo += fijo * cantidad
            precio_actual = datos.precio_neto(linea)
            try:
                actual = numero(precio_actual) * (1 + IVA if con_iva else 1)
            except ValueError:
                actual = None
            filas.append((None, nombre, codigo, (clp(costo), clp(fijo),
                                                 clp(actual) if actual is not None else "—",
                                                 clp(resultado["precio_con_iva" if con_iva else "precio_neto"]))))
        if not esperadas:
            st.caption("Incluye al menos un producto para calcular precios.")
            return
        if filas:
            modo = "con IVA" if con_iva else "neto"
            listados.mostrar(("Producto", "Costo directo", "Costo fijo", f"Precio actual {modo}",
                              f"Precio sugerido {modo}"), filas,
                             key="cf_proyeccion", etiqueta="Importes por unidad")
            if len(sugeridos) == esperadas:
                st.caption(f"Esta cotización recuperaría {clp(importe_fijo)} del gasto fijo del mes.")
        if len(sugeridos) < esperadas:
            st.caption("Completa los costos faltantes para aplicar los precios de toda la cotización.")
        if st.button("Aplicar precios a esta cotización", type="primary", icon=":material/check:",
                     disabled=bloqueado or len(sugeridos) < esperadas, key="cf_aplicar"):
            aplicar(sugeridos)
