"""Tablas de lectura con enlaces a las fichas, sin selección intermedia."""
from html import escape
from urllib.parse import urlencode

import streamlit as st


_ESTILOS = """
:host {color:var(--st-text-color)}
.marco {border:var(--gv-list-frame,1px solid var(--gv-line,#2b3a56));
        border-radius:var(--gv-list-radius,13px);overflow:hidden}
table {border-collapse:collapse;width:100%;font:inherit;line-height:var(--gv-list-line-height,inherit);text-align:left}
th {background:var(--gv-list-heading,var(--gv-panel-strong,#18253d));color:var(--gv-muted,#a8b7d0);
    font-size:.77rem;font-weight:650;padding:var(--gv-list-cell-padding,.7rem 1rem);white-space:nowrap}
td {padding:var(--gv-list-cell-padding,.72rem 1rem);border-top:1px solid var(--gv-line,#2b3a56);overflow-wrap:anywhere}
tbody tr {background:var(--gv-panel,#111a2a);transition:background .16s ease}
tbody tr[data-link] {cursor:pointer}
tbody tr[data-link]:hover,tbody tr:focus-within {background:var(--gv-panel-strong,#18253d)}
a {color:var(--gv-link,#a8bcff);font-weight:700;text-decoration:none}
a:hover,a:focus-visible {text-decoration:underline}
a:focus-visible {outline:2px solid currentColor;outline-offset:4px;border-radius:2px}
small {display:block;margin-top:.15rem;color:var(--gv-muted,#a8b7d0);font-size:.78rem}
.importe {text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.estado {display:inline-block;font-size:.75rem;font-weight:650;padding:.15rem .53rem;
         border:1px solid var(--gv-list-badge-border,currentColor);
         background:var(--gv-list-badge-bg,transparent);border-radius:1rem;white-space:nowrap}
.ambar {color:#f5c675}.azul {color:#a8bcff}.verde {color:#78e0bd}.rojo {color:#ff9eaa}
@media (max-width:680px) {
  thead {position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
  table,tbody,tr {display:block}
  tbody tr {display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);padding:.58rem .8rem;
            gap:.28rem .72rem;border-top:1px solid var(--gv-line,#2b3a56)}
  tbody tr:first-child {border-top:0}
  td {display:block;border:0;padding:0;font-size:.84rem}
  td:first-child {grid-column:1/-1;font-size:.95rem}
  td:not(:first-child)::before {content:attr(data-label);display:block;font-size:.7rem;
                                color:var(--gv-muted,#a8b7d0)}
  td.importe {text-align:left}
}
@media (prefers-reduced-motion:reduce) {tbody tr {transition:none}}
"""
_TABLA = st.components.v2.component(
    "giovalia_tabla_fichas", html='<div class="marco"></div>', css=_ESTILOS,
    js="""
export default function ({data, parentElement, setTriggerValue}) {
    const marco = parentElement.querySelector('.marco');
    marco.innerHTML = data.tabla;
    marco.onclick = (event) => {
        if (event.defaultPrevented || event.button !== 0 || event.ctrlKey ||
            event.metaKey || event.shiftKey || event.altKey) return;
        const fila = event.target.closest('tr[data-link]');
        if (!fila || marco.getRootNode().getSelection?.()?.toString()) return;
        const enlace = fila.querySelector('a');
        event.preventDefault();
        setTriggerValue('ficha', enlace.dataset.id);
    };
    return () => { marco.onclick = null; };
}
""",
)

TONO_ESTADO = {"Borrador": "ambar", "Enviada": "azul", "Aceptada": "verde", "Rechazada": "rojo"}


def mostrar(columnas, filas, *, key, etiqueta, ruta=None, abrir=None):
    """Cada fila tiene id, título, subtítulo y celdas; el id puede faltar."""
    def celda(columna, valor):
        clase = "importe" if columna.startswith(("Total", "Precio", "Costo", "Subtotal")) else ""
        if isinstance(valor, tuple):
            texto, tono = valor
            contenido = f'<span class="estado {escape(tono)}">{escape(str(texto))}</span>'
        else:
            contenido = escape(str(valor))
        return f'<td class="{clase}" data-label="{escape(columna)}">{contenido}</td>'

    ids = {}
    cuerpo = []
    for identificador, titulo, subtitulo, valores in filas:
        nombre = escape(str(titulo))
        enlazado = identificador is not None and ruta is not None
        if enlazado:
            ids[str(identificador)] = identificador
            href = escape("?" + urlencode({ruta: identificador}))
            nombre = f'<a href="{href}" data-id="{escape(str(identificador))}">{nombre}</a>'
        detalle = f'<small>{escape(str(subtitulo))}</small>' if subtitulo else ""
        celdas = "".join(celda(col, valor) for col, valor in zip(columnas[1:], valores))
        cuerpo.append(f"<tr{' data-link' if enlazado else ''}><td>{nombre}{detalle}</td>{celdas}</tr>")
    cabecera = "".join(
        f'<th scope="col" class="{ "importe" if col.startswith(("Total", "Precio", "Costo", "Subtotal")) else ""}">{escape(col)}</th>'
        for col in columnas
    )
    tabla = (f'<table aria-label="{escape(etiqueta)}"><thead><tr>{cabecera}</tr></thead>'
             f'<tbody>{"".join(cuerpo)}</tbody></table>')

    def seguir():
        ficha = st.session_state.get(key, {}).get("ficha")
        if isinstance(ficha, str) and ficha in ids:
            abrir(ids[ficha])

    _TABLA(key=key, data={"tabla": tabla}, on_ficha_change=seguir if abrir else None, width="stretch")


def cotizaciones(registros, abrir, pesos, fecha, *, key, privacidad=False, compacta=False):
    columnas = ("Cotización", "Estado", "Total")
    if not compacta:
        columnas += ("Fecha",) if privacidad else ("Fecha", "Cliente")
    filas = []
    for r in registros:
        estado = r.get("estado") or "Borrador"
        dia = fecha(r.get("fecha") or "")
        valores = [(estado, TONO_ESTADO.get(estado, "azul")), pesos(r.get("total") or 0)]
        if not compacta:
            valores.append(dia or "—")
            if not privacidad:
                valores.append(r.get("cliente") or "—")
        filas.append((r["id"], r["numero"], dia if compacta else "", valores))
    mostrar(columnas, filas, key=key, etiqueta="Cotizaciones", ruta="cotizacion", abrir=abrir)
