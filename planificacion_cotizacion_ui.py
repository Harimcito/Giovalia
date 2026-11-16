"""Controles de presupuesto y revisión de fichas en el editor."""
from datetime import date
from decimal import Decimal
from hashlib import sha256

import streamlit as st

import canastas_cotizacion as canastas
import evidencia_cotizacion as evidencia
import flujo_cotizacion as flujo
from cotizacion_opciones import cantidad_valida
from edicion_lineas import ID
from interfaz_giovalia import fragmento_pdf
from revisor import totales_de
from stock_catalogo import datos_stock
from ui_montos import clp, leer


def _opcion(linea, codigo, ficha):
    neto = canastas.costo_linea(linea)
    stock = datos_stock(ficha)
    existencias = None
    if (codigo and stock["stock_confirmado_el"] == date.today() and stock["estado_stock"] == flujo.EN_STOCK
            and linea.get("disponibilidad") != flujo.A_PEDIDO and flujo.plazo_efectivo(linea) in (None, 0)):
        existencias = cantidad_valida(ficha.get("stock_cantidad"))
    return None if neto is None else canastas.Opcion(codigo or "", neto, existencias, linea)


def _reemplazo(linea, codigo, ficha):
    precio = ficha.get("precio_neto")
    stock = datos_stock(ficha)
    estado = stock["estado_stock"] if stock["stock_confirmado_el"] else flujo.POR_CONFIRMAR
    return {**linea, "clave_auto": codigo, "descripcion": ficha.get("nombre") or codigo,
            "unitario": precio, "precio_neto": precio, "origen": "descripcion",
            **flujo.datos_entrega(estado, ficha.get("reposicion_dias") if estado == flujo.A_PEDIDO else None)}


def presupuesto(lineas, bib, cot_id):
    panel = st.expander("Armar canastas por presupuesto", icon=":material/shopping_basket:",
                        key=f"canastas_{cot_id}", on_change="rerun")
    if not panel.open:
        return None
    with panel:
        incluidas = [ln for ln in lineas if ln.get("incluir", True)]
        solicitadas = [ln for ln in incluidas if ln.get("es_producto", True)]
        if not solicitadas or len(solicitadas) > 12:
            st.info("Incluye entre 1 y 12 líneas de producto para armar canastas.")
            return None
        adicionales = [canastas.costo_linea(ln) for ln in incluidas if not ln.get("es_producto", True)]
        if any(costo is None for costo in adicionales):
            st.warning("Completa el precio de los cargos incluidos, como el despacho.")
            return None
        base = sum(adicionales, Decimal(0))
        escrito = st.text_input("Presupuesto máximo con IVA", key=f"monto_canasta_{cot_id}", placeholder="120.000")
        st.caption("Cada canasta cubre líneas completas. «Entrega hoy» usa cantidades confirmadas hoy.")
        try:
            maximo = leer(escrito)
            if maximo is not None and maximo <= 0:
                raise ValueError("Ingresa un presupuesto mayor que cero.")
        except ValueError as exc:
            st.warning(str(exc))
            return None
        if maximo is None:
            return None
        catalogo = {c: f"{c} · {f.get('nombre') or c}" for c, f in bib.indice.items()
                    if cantidad_valida(f.get("precio_neto")) is not None}
        pedidos = []
        for i, linea in enumerate(solicitadas, 1):
            clave = f"{cot_id}_{linea[ID]}"
            st.text(f"{i}. {linea['descripcion']} · {linea['cantidad']:g}")
            imprescindible = st.checkbox("Imprescindible", value=True, key=f"canasta_necesaria_{clave}")
            codigo = bib.clave_vigente(linea.get("clave_auto") or "")
            codigos = st.multiselect("Reemplazos permitidos", [c for c in catalogo if c != codigo],
                                     format_func=catalogo.__getitem__, max_selections=4,
                                     key=f"canasta_alternativas_{clave}")
            opciones = [_opcion(linea, codigo, bib.indice.get(codigo, {}))]
            opciones.extend(_opcion(_reemplazo(linea, c, bib.indice[c]), c, bib.indice[c]) for c in codigos)
            pedidos.append(canastas.Pedido(imprescindible, cantidad_valida(linea["cantidad"]) or Decimal(0),
                                           [opcion for opcion in opciones if opcion is not None]))

        huella = sha256(repr((pedidos, maximo, base)).encode()).hexdigest()
        estado = st.session_state
        if st.button("Comparar tres canastas", type="primary"):
            try:
                estado["_resultado_canastas"] = huella, canastas.planificar(pedidos, maximo, base)
            except ValueError as exc:
                st.error(str(exc))
                return None
        clave, resultados = estado.get("_resultado_canastas", (None, {}))
        if clave != huella:
            return None
        if not resultados:
            st.warning("Ninguna línea con precio cabe en ese presupuesto.")
            return None
        obligatorias = sum(p.imprescindible for p in pedidos)
        for col, (nombre, canasta) in zip(st.columns(3), resultados.items()):
            with col:
                st.subheader(nombre)
                if canasta is None:
                    st.caption("No hay una combinación con stock confirmado hoy dentro del presupuesto.")
                    continue
                st.metric("Total con IVA", clp(canasta.total))
                st.caption(f"Quedan {clp(maximo - canasta.total)} · {canasta.imprescindibles}/{obligatorias} imprescindibles.")
                for linea, opcion in zip(solicitadas, canasta.elegidas):
                    if opcion is None:
                        st.text(f"Queda fuera · {linea['descripcion']}")
                    else:
                        st.text(f"{opcion.linea['cantidad']:g} × {opcion.linea['descripcion']}")
                if st.button("Aplicar esta canasta", key=f"aplicar_canasta_{nombre}_{cot_id}", width="stretch"):
                    cambios = {ln[ID]: op for ln, op in zip(solicitadas, canasta.elegidas)}
                    nuevas = [ln if ln[ID] not in cambios else
                              cambios[ln[ID]].linea if cambios[ln[ID]] else {**ln, "incluir": False}
                              for ln in lineas]
                    incluidas_nuevas = []
                    for ln in nuevas:
                        if ln.get("incluir", True):
                            unitario = ln.get("unitario")
                            incluidas_nuevas.append({"cantidad": ln["cantidad"], "precio_neto":
                                                     ln.get("precio_neto") if unitario is None else unitario})
                    if totales_de(incluidas_nuevas)["total"] > maximo:
                        st.error("El total redondeado de la cotización supera el presupuesto. Ajusta el límite.")
                        return None
                    return nuevas
    return None


@st.cache_data(max_entries=32, show_spinner=False)
def _leer_ficha(contenido):
    return evidencia.leer_paginas(contenido)


def revisar_ficha(linea, resolver):
    requisitos = evidencia.dividir_requisitos(linea.get(evidencia.CAMPO, ""))
    codigo = linea.get("clave_auto") or ""
    ficha = resolver(codigo, True)[1] if codigo else None
    contenido = None
    aviso = "La línea no tiene una ficha PDF seleccionada."
    paginas = []
    if ficha:
        try:
            with ficha.open("rb") as archivo:
                contenido = archivo.read(evidencia.MAX_BYTES + 1)
            if len(contenido) > evidencia.MAX_BYTES:
                aviso = "La ficha supera 12 MB. Revisa los requisitos manualmente."
                contenido = None
            else:
                paginas, aviso = _leer_ficha(contenido)
        except OSError:
            aviso = "El archivo de la ficha no está disponible."
    return contenido, aviso, evidencia.coincidencias(requisitos, paginas)


def alertas_evidencia(lineas, resolver):
    alertas = []
    for indice, linea in enumerate(lineas):
        if linea.get(evidencia.CAMPO) and linea.get("incluir", True) and linea.get("es_producto", True):
            _, aviso, resultados = revisar_ficha(linea, resolver)
            alertas.extend((indice, f"Requisito sin evidencia textual · {linea['descripcion']} · {requisito}")
                           for requisito, paginas in resultados.items() if not paginas)
            if aviso:
                alertas.append((indice, f"{linea['descripcion']} · {aviso}"))
    return alertas


def requisitos(lineas, resolver, cot_id, *, editar):
    candidatas = [ln for ln in lineas if ln.get("incluir", True) and ln.get("es_producto", True)
                  and (editar or ln.get(evidencia.CAMPO))]
    if not candidatas:
        return None
    panel = st.expander("Evidencia por requisito", icon=":material/fact_check:",
                        key=f"evidencia_{cot_id}_{int(editar)}", on_change="rerun")
    if not panel.open:
        return None
    with panel:
        etiquetas = {ln[ID]: f"{i}. {ln['descripcion']}" for i, ln in enumerate(candidatas, 1)}
        elegida = st.selectbox("Producto a revisar", etiquetas, format_func=etiquetas.__getitem__,
                              key=f"evidencia_linea_{cot_id}_{int(editar)}")
        linea = next(ln for ln in candidatas if ln[ID] == elegida)
        if editar:
            with st.form(f"requisitos_ficha_{cot_id}_{elegida}"):
                texto = st.text_area("Requisitos técnicos, uno por línea", max_chars=1000,
                                     value=linea.get(evidencia.CAMPO, ""),
                                     key=f"texto_requisitos_{cot_id}_{elegida}",
                                     placeholder="Oficio\n500 hojas\n75 g/m²")
                if st.form_submit_button("Guardar requisitos"):
                    guardados = evidencia.dividir_requisitos(texto)
                    if len(guardados) > 12:
                        st.warning("Ingresa hasta 12 requisitos por línea de producto.")
                    else:
                        return [{**ln, evidencia.CAMPO: "\n".join(guardados)} if ln[ID] == elegida else ln
                                for ln in lineas]
        if not linea.get(evidencia.CAMPO):
            return None
        contenido, aviso, resultados = revisar_ficha(linea, resolver)
        st.caption("Las coincidencias indican dónde aparece el texto. Comprueba en la página que corresponde al producto.")
        if aviso:
            st.warning(aviso)
        numeros = set()
        for requisito, paginas in resultados.items():
            st.text(requisito)
            if not paginas:
                st.warning("Revisión manual · sin coincidencia textual en la ficha.")
            for pagina, extracto in paginas[:3]:
                numeros.add(pagina)
                st.caption(f"Página {pagina} · {extracto}")
        if numeros and contenido:
            numero = st.selectbox("Abrir página de evidencia", sorted(numeros), index=None,
                                  key=f"evidencia_pagina_{cot_id}_{elegida}_{int(editar)}")
            if numero is not None:
                try:
                    hoja = fragmento_pdf(contenido, numero - 1, numero)
                except Exception:
                    st.warning("No se pudo abrir esta página. Consulta la ficha original.")
                else:
                    try:
                        st.pdf(hoja, height=420, alt=f"Ficha técnica · página {numero}")
                    except Exception:
                        st.download_button("Descargar página de evidencia", hoja, file_name=f"ficha-pagina-{numero}.pdf",
                                           mime="application/pdf", on_click="ignore")
    return None
