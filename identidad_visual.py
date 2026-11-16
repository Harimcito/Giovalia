"""Sistema visual de Giovalia. HTML decorativo; las acciones siguen siendo widgets nativos."""
from html import escape
from pathlib import Path
from base64 import b64encode
import streamlit as st


def estilos():
    st.html(Path(__file__).with_name("giovalia.css"))


def marca():
    logo = _imagen_svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" fill="none"><path d="M22 8.2A10 10 0 1 0 23.7 17H14v-5h13v5.5A13.1 13.1 0 1 1 24.1 5L22 8.2Z" fill="white"/></svg>')
    st.html(f'<div class="gv-brand"><div class="gv-mark" aria-hidden="true">{logo}</div><div><div class="gv-name">Giovalia</div><div class="gv-sub">Espacio comercial</div></div></div>')


def topbar(seccion, usuario="", *, compartida=False):
    logo = Path(__file__).with_name("logo_gioval_catalogo.png")
    marca = '<strong>Giovalia</strong>'
    if logo.is_file():
        imagen = b64encode(logo.read_bytes()).decode("ascii")
        marca = f'<img src="data:image/png;base64,{imagen}" alt="Gioval" width="112">'
    cuenta = f'<span>Hola, {escape(usuario)}</span>' if usuario else ""
    biblioteca = "Biblioteca compartida" if compartida else "Biblioteca local"
    st.html(f'<div class="gv-topbar">{marca}<div class="gv-contexto">'
            f'<div class="gv-breadcrumb"><strong>{escape(str(seccion))}</strong></div>'
            f'<div class="gv-cuenta">{cuenta}<span>{biblioteca}</span></div>'
            '</div></div>')


def titulo(texto, detalle, categoria="Espacio comercial", icono="↗"):
    st.html(f'<div class="gv-page"><div><div class="gv-kicker">{escape(str(categoria))}</div><h1>{escape(str(texto))}</h1><p>{escape(str(detalle))}</p></div><div class="gv-page-icon" aria-hidden="true">{escape(str(icono))}</div></div>')


def _imagen_svg(svg, clase=""):
    """Las imágenes SVG sobreviven al saneamiento de st.html; SVG inline no."""
    contenido = b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="{escape(clase)}" src="data:image/svg+xml;base64,{contenido}" alt="" draggable="false">'


def _flujo():
    conectores = _imagen_svg('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 216" width="320" height="216">
      <style>.route{fill:none;stroke:#6377ae;stroke-width:1;opacity:.45}.packet{fill:none;stroke:#93a7ff;stroke-width:2;stroke-dasharray:12 190;stroke-linecap:round;animation:packet 6s linear infinite}.two{stroke:#68d8c3;animation-delay:-3s}@keyframes packet{to{stroke-dashoffset:-404}}@media(prefers-reduced-motion:reduce){.packet{animation:none}}</style>
      <path class="route" d="M48 43H156Q173 43 173 68V110H281"/><path class="route" d="M43 198H130Q143 198 143 182V107H281"/>
      <path class="packet" d="M48 43H156Q173 43 173 68V110H281"/><path class="packet two" d="M43 198H130Q143 198 143 182V107H281"/></svg>''', "gv-flow-svg")
    return f'''<div class="gv-art" aria-hidden="true"><div class="gv-art-grid"></div>{conectores}<div class="gv-sheet"><div class="gv-sheet-head"><span>PROPUESTA</span><span class="gv-mini-mark"><i></i><i></i><i></i></span></div><div class="gv-row"><i></i><b></b><b></b></div><div class="gv-row"><i></i><b></b><b></b></div><div class="gv-row"><i></i><b></b><b></b></div><div class="gv-chart"><i style="height:35%"></i><i></i><i></i><i></i><i></i></div></div><div class="gv-node one"><i></i>Pedido</div><div class="gv-node two"><i></i>Productos</div><div class="gv-node three"><i></i>Costos y margen</div></div>'''


def hero(titulo="Tu operación,<br><span>con una nueva perspectiva.</span>", detalle=None, compacto=False):
    # Únicamente dos etiquetas tipográficas se aceptan en el título compatible.
    titulo = escape(str(titulo))
    for etiqueta in ("br", "span", "/span"):
        titulo = titulo.replace(escape(f"<{etiqueta}>"), f"<{etiqueta}>")
    detalle = detalle or "Conecta tus productos, propuestas y números. Cada oportunidad empieza con una decisión bien informada."
    clase = "gv-hero gv-compact" if compacto else "gv-hero"
    st.html(f'<section class="{clase}" aria-label="Giovalia, gestión comercial"><div class="gv-eyebrow"><i aria-hidden="true"></i>Tu negocio, conectado</div><h1>{titulo}</h1><p>{escape(str(detalle))}</p><div class="gv-chips"><span class="gv-chip">Cotizaciones</span><span class="gv-chip">Productos</span><span class="gv-chip">Rentabilidad</span></div>{_flujo()}</section>')


def section_label(titulo, detalle=""):
    st.html(f'<div class="gv-section-heading"><h2>{escape(str(titulo))}</h2><span>{escape(str(detalle))}</span></div>')


def empty_state(titulo, detalle, icono="▤"):
    st.html(f'<div class="gv-empty"><div class="gv-empty-icon" aria-hidden="true">{escape(str(icono))}</div><h3>{escape(str(titulo))}</h3><p>{escape(str(detalle))}</p></div>')


def card_copy(titulo, detalle, icono="↗", tono="indigo"):
    tono = tono if tono in {"indigo", "teal", "amber"} else "indigo"
    st.html(f'<div class="gv-card-copy"><div class="gv-card-icon {tono}" aria-hidden="true">{escape(str(icono))}</div><h3>{escape(str(titulo))}</h3><p>{escape(str(detalle))}</p></div>')


def activity_row(titulo, estado="", monto="", fecha="", *, detalle=None):
    # Compatibilidad con la firma anterior (título, detalle, estado).
    if not fecha and monto in {"Borrador", "Enviada", "Aceptada", "Rechazada", "Completada"}:
        detalle, estado, monto = estado, monto, ""
    clase = {
        "Borrador": "draft", "Enviada": "sent", "Aceptada": "accepted",
        "Completada": "accepted", "Rechazada": "rejected",
    }.get(estado, "")
    if isinstance(monto, (int, float)):
        monto = "$" + format(monto, ",.0f").replace(",", ".")
    secundario = f'<small>{escape(str(detalle))}</small>' if detalle else ""
    valor = f'<div class="gv-activity-value"><strong>{escape(str(monto))}</strong><small>{escape(str(fecha))}</small></div>' if monto or fecha else ""
    st.html(f'<div class="gv-activity"><div class="gv-activity-mark" aria-hidden="true">▤</div><div class="gv-activity-copy"><strong>{escape(str(titulo))}</strong>{secundario}</div><span class="gv-status {clase}">{escape(str(estado))}</span>{valor}</div>')


def pasos(actual, nombres):
    """Stepper de lectura: las validaciones y los botones de avance pertenecen al flujo."""
    nombres = tuple(str(nombre) for nombre in nombres)
    if not nombres:
        return
    actual = max(1, min(int(actual), len(nombres)))
    total = len(nombres)
    identidad = (str(st.session_state.get("cot_id", "actual")), nombres)
    anterior = st.session_state.get("_gv_visual_step", {})
    paso_anterior = anterior.get("paso", actual) if anterior.get("identidad") == identidad else actual
    st.session_state["_gv_visual_step"] = {"identidad": identidad, "paso": actual}
    divisor = max(1, total - 1)
    desde = max(0, min(100, (paso_anterior - 1) / divisor * 100))
    hasta = (actual - 1) / divisor * 100
    items = []
    for numero, nombre in enumerate(nombres, 1):
        estado, etiqueta = ("complete", "Completado") if numero < actual else ("active", "En curso") if numero == actual else ("future", "Pendiente")
        simbolo = "✓" if numero < actual else str(numero)
        current = ' aria-current="step"' if numero == actual else ""
        items.append(f'<li class="gv-step {estado}"{current}><span class="gv-step-circle" aria-hidden="true">{simbolo}</span><span class="gv-step-label">{escape(nombre)}</span><span class="gv-step-status">{etiqueta}</span></li>')
    resumen = f"Etapa {actual} de {total}: {nombres[actual - 1]}"
    st.html(f'<section class="gv-stepper" aria-label="Etapas de la cotización" style="--gv-step-count:{total};--gv-step-from:{desde:.3f}%;--gv-step-to:{hasta:.3f}%"><div class="gv-step-track" aria-hidden="true"><div class="gv-step-track-fill"></div></div><ol class="gv-steps" role="list">{"".join(items)}</ol><span class="gv-sr-only" role="status" aria-live="polite">{escape(resumen)}</span></section>')


def inicio_de_seccion(identidad):
    """Solo al navegar: llevar al inicio del nuevo espacio, sin intervenir campos."""
    identidad = int(identidad)
    st.html(f'''<script data-giovalia-route="{identidad}">
      requestAnimationFrame(() => requestAnimationFrame(() => {{
        const main = document.querySelector('[data-testid="stMain"]');
        if (main) main.scrollTo({{top: 0, behavior: 'instant'}});
      }}));
    </script>''', unsafe_allow_javascript=True)
