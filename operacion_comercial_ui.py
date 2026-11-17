"""Pantallas de apoyo para cotizaciones y operación de la biblioteca."""
from datetime import date
from hashlib import sha256
import json
import sqlite3
import zipfile

import pandas as pd
import streamlit as st

import operacion_comercial as op
import seo_geo
from historial import ConflictoActualizacion
from revisor import clp


def margen_pedido(lineas, bib, hist=None, cot_id=None):
    panel = st.expander("Margen y costos del pedido", key="panel_margen_pedido", on_change="rerun")
    if not panel.open:
        return
    with panel:
        if not lineas:
            st.caption("Agrega productos para estimar el margen.")
            return
        llave = f"margen_pedido_{cot_id}" if cot_id else "margen_pedido"
        base = json.loads(hist.config(llave, "{}")) if hist and cot_id else {}
        a, b, c, d = st.columns(4)
        despacho = a.number_input("Costo de despacho neto", min_value=0, step=100,
                                  value=int(base.get("despacho", 0)), key=f"{llave}_despacho")
        otros = b.number_input("Otros costos netos", min_value=0, step=100,
                               value=int(base.get("otros", 0)), key=f"{llave}_otros")
        descuento = c.number_input("Descuento neto", min_value=0, step=100,
                                   value=int(base.get("descuento", 0)), key=f"{llave}_descuento")
        objetivo = d.number_input("Margen objetivo (%)", min_value=0, max_value=99,
                                  value=int(base.get("objetivo", 20)), key=f"{llave}_objetivo")
        try:
            resultado = op.calcular_margen(lineas, bib.indice, despacho, descuento, otros, objetivo)
        except ValueError as error:
            st.warning(str(error))
            return
        a, b, c, d = st.columns(4)
        a.metric("Venta neta simulada", clp(resultado["venta"]))
        b.metric("Costos conocidos", clp(resultado["egresos"]))
        margen = resultado["margen"]
        c.metric("Margen real", f"{margen:.1f} %" if margen is not None else "Pendiente")
        minimo = resultado["minimo"]
        d.metric("Venta mínima objetivo", clp(minimo) if minimo is not None else "Pendiente")
        if resultado["utilidad"] is not None:
            st.caption(f"Utilidad neta estimada: {clp(resultado['utilidad'])}.")
        if resultado["faltantes"]:
            st.warning(" · ".join(resultado["faltantes"]))
        st.caption("Simulación interna. Para reflejar un descuento o cobro en el documento, edita la cotización.")
        if hist and cot_id and st.button("Guardar supuestos de margen", key=f"{llave}_guardar"):
            hist.set_config(llave, json.dumps(
                {"despacho": despacho, "otros": otros, "descuento": descuento, "objetivo": objetivo}
            ))
            st.success("Supuestos guardados para este pedido.")


def proveedores(bib, hist):
    st.subheader("Listas de costos por proveedor")
    st.caption("Sube CSV o XLSX con código y costo neto. Puedes guardar la lista como referencia para futuras comparaciones.")
    st.download_button("Plantilla CSV", "codigo;costo_neto\n", file_name="costos_proveedor.csv",
                       mime="text/csv", on_click="ignore")
    nombre = st.text_input("Proveedor", max_chars=120, key="proveedor_lista_nombre").strip()
    archivo = st.file_uploader("Lista de costos netos", type=["csv", "xlsx"], key="lista_proveedor_archivo")
    if not archivo or not nombre:
        return
    contenido = archivo.getvalue()
    llave = (nombre.casefold(), sha256(contenido).hexdigest())
    if st.session_state.get("_lista_proveedor_revision", {}).get("llave") != llave:
        try:
            lista = op.cargar_costos(archivo.name, contenido)
        except (ValueError, OSError, zipfile.BadZipFile) as error:
            st.error(str(error))
            return
        st.session_state["_lista_proveedor_revision"] = {
            "llave": llave, "previa": hist.lista_proveedor(llave[0]), "lista": lista,
        }
    datos = st.session_state["_lista_proveedor_revision"]
    comparacion = op.comparar_costos(bib.indice, datos["lista"], datos["previa"])
    st.dataframe(comparacion, width="stretch", hide_index=True)
    if st.button("Guardar lista del proveedor", key="confirmar_lista_proveedor"):
        try:
            hist.guardar_lista_proveedor(llave[0], datos["lista"], datos["previa"])
        except ConflictoActualizacion as error:
            st.session_state.pop("_lista_proveedor_revision", None)
            st.error(str(error))
        else:
            datos["previa"] = datos["lista"]
            st.success("Lista guardada. Puedes usar la Biblioteca para modificar costos del catálogo.")


def historial_clientes(hist, abrir, *, privado):
    st.subheader("Historial comercial y seguimiento")
    if privado:
        st.caption("La vista de seguimiento utiliza los números de cotización del historial anonimizado.")
    registros = hist.listar(incluir_datos=False)
    seguimientos = hist.seguimientos()
    if not registros:
        st.info("Todavía no hay cotizaciones.")
        return
    clientes = {
        op.identidad_cliente(r["cliente"], r["rut"]): f"{r['cliente']} · {r['rut']}" if r["rut"] else r["cliente"]
        for r in registros if r["cliente"] or r["rut"]
    }
    if clientes:
        cliente = st.selectbox("Cliente", [None, *clientes], format_func=lambda x: clientes.get(x, "Todos"),
                               key="segmento_cliente_comercial")
        if cliente:
            registros = [r for r in registros if op.identidad_cliente(r["cliente"], r["rut"]) == cliente]
    filtro = st.selectbox("Vigencia o estado", ["Todas", "Vence pronto", "Vencida", "Vigente",
                                               "Sin vigencia", "Pendiente", "Aceptada", "Rechazada"],
                          key="filtro_seguimiento")
    filas = []
    opciones = {}
    for reg in registros:
        seguimiento = seguimientos.get(reg["id"], {})
        vigencia = op.estado_seguimiento(seguimiento.get("vence"))
        pendiente = reg["estado"] in ("Borrador", "Enviada")
        if filtro in ("Vence pronto", "Vencida") and (not pendiente or vigencia != filtro):
            continue
        if filtro in ("Aceptada", "Rechazada") and reg["estado"] != filtro:
            continue
        if filtro == "Pendiente" and not pendiente:
            continue
        if filtro in ("Vigente", "Sin vigencia") and vigencia != filtro:
            continue
        opciones[reg["id"]] = reg
        filas.append({"Cotización": reg["numero"], "Fecha": reg["fecha"], "Cliente": reg["cliente"],
                      "Estado": reg["estado"], "Total": clp(reg["total"] or 0),
                      "Vence": seguimiento.get("vence"), "Vigencia": vigencia,
                      "Último contacto": seguimiento.get("ultimo_contacto")})
    if not filas:
        st.info("No hay cotizaciones que coincidan con el filtro.")
        return
    st.dataframe(filas, width="stretch", hide_index=True)
    cot_id = st.selectbox("Cotización para seguimiento", list(opciones),
                          format_func=lambda i: opciones[i]["numero"], key="cotizacion_seguimiento")
    actual = seguimientos.get(cot_id, {})
    reg = opciones[cot_id]

    def fecha(campo):
        return date.fromisoformat(actual[campo]) if actual.get(campo) else None

    with st.form(f"seguimiento_{cot_id}"):
        a, b = st.columns(2)
        vence = a.date_input("Vigencia hasta", fecha("vence"), format="DD/MM/YYYY")
        contacto = b.date_input("Último contacto", fecha("ultimo_contacto"), format="DD/MM/YYYY")
        if st.form_submit_button("Guardar seguimiento"):
            hist.guardar_seguimiento(cot_id, vence.isoformat() if vence else None,
                                     contacto.isoformat() if contacto else None)
            st.rerun()
    mensaje = op.mensaje_seguimiento(reg["numero"], reg["cliente"], actual.get("vence"))
    st.text_area("Mensaje para revisar y copiar", value=mensaje, key=f"mensaje_{cot_id}_{actual.get('vence')}")
    st.button("Usar como base de una cotización", on_click=abrir, args=(cot_id, False), key=f"base_comercial_{cot_id}")


def auditoria(hist):
    st.subheader("Registro de cambios")
    filas = hist.cambios_comerciales()
    if not filas:
        st.info("Los cambios registrados aparecerán aquí.")
        return
    entidad = st.selectbox("Tipo de cambio", ["Todos", *sorted({r["entidad"] for r in filas})],
                           key="auditoria_entidad")
    st.dataframe([fila for fila in filas if entidad == "Todos" or fila["entidad"] == entidad],
                 width="stretch", hide_index=True)


def previsualizar_ficha(bib):
    st.subheader("Vista previa SEO para gioval.cl")
    opciones = sorted(bib.indice)
    if not opciones:
        st.info("Agrega primero un producto a la biblioteca.")
        return
    clave = st.selectbox("Producto", opciones, format_func=lambda x: bib.indice[x].get("nombre", x),
                         key="ficha_seo_vista_previa")
    ficha = bib.indice[clave]
    borrador = seo_geo.borradores_editoriales(ficha, clave)
    url = ficha.get("seo_url_publica") or f"https://gioval.cl/{borrador['slug']}"
    st.caption(url)
    st.subheader(borrador["titulo_seo"])
    st.write(borrador["descripcion_seo"])
    foto, _ = bib.archivos(clave)
    if foto:
        st.image(str(foto), width=220, alt=borrador["texto_alternativo_imagen"])
    st.caption(f"Texto alternativo: {borrador['texto_alternativo_imagen']}")
    if ficha.get("seo_imagen_url"):
        st.caption(f"Imagen pública configurada: {ficha['seo_imagen_url']}")
    st.caption("Vista previa interna. La publicación se realiza en el sitio.")
    st.code(json.dumps(seo_geo.producto_jsonld(clave, ficha), ensure_ascii=False, indent=2), language="json")


def verificar_respaldo_ui():
    st.subheader("Verificar un respaldo")
    respaldo = st.file_uploader("Respaldo de Giovalia", type=["zip"], key="respaldo_qa_zip",
                                max_upload_size=300)
    if respaldo and st.button("Restaurar y verificar en copia temporal", key="respaldo_qa_verificar"):
        try:
            resultado = op.verificar_respaldo(respaldo.getvalue())
        except (ValueError, OSError, KeyError, zipfile.BadZipFile, sqlite3.DatabaseError) as error:
            st.error(f"No se pudo verificar el respaldo. {error}")
        else:
            st.write({k: v for k, v in resultado.items() if k != "faltantes"})
            if resultado["faltantes"]:
                st.warning("Hay archivos referenciados que faltan en el respaldo.")
                st.dataframe(pd.DataFrame({"Faltantes": resultado["faltantes"]}), hide_index=True)
            else:
                st.success("Se recuperaron las tablas y las referencias del respaldo en la copia temporal.")


def render(bib, hist, abrir, *, privado=False, admin=True, vista_precios=None):
    st.title("Gestión comercial")
    opciones = ["Clientes y vigencia", "Proveedores", "Cambios", "Vista SEO"]
    if admin:
        opciones.append("Verificar respaldo")
    if vista_precios:
        opciones.append("Simular precios")
    vista = st.segmented_control("Herramienta", opciones, default=opciones[0], required=True, key="gestion_vista")
    if vista == "Clientes y vigencia":
        historial_clientes(hist, abrir, privado=privado)
    elif vista == "Proveedores":
        proveedores(bib, hist)
    elif vista == "Cambios":
        auditoria(hist)
    elif vista == "Vista SEO":
        previsualizar_ficha(bib)
    elif vista == "Verificar respaldo":
        verificar_respaldo_ui()
    else:
        vista_precios(bib, hist)
