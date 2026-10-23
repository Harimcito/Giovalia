"""Shared desktop and Streamlit presentation helpers for Gioval."""
from __future__ import annotations

import base64
import html
from pathlib import Path


PROFILE_LIST_KEY = "_gioval_profiles"
PROFILE_KEY = "_gioval_active_profile"
PROFILE_SELECTOR_KEY = "gioval_profile_selector"
NEW_PROFILE_KEY = "_gioval_new_profile"
DEFAULT_PROFILE = "Giovanna"
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "gioval-mark.svg"


def profile_names(state) -> list[str]:
    """Return profile names scoped to the current Streamlit browser session."""
    names = state.get(PROFILE_LIST_KEY)
    if not isinstance(names, list):
        names = [DEFAULT_PROFILE]
        state[PROFILE_LIST_KEY] = names
    cleaned = []
    for name in names:
        value = str(name).strip()[:40]
        if value and value.casefold() not in {item.casefold() for item in cleaned}:
            cleaned.append(value)
    if not cleaned:
        cleaned = [DEFAULT_PROFILE]
    state[PROFILE_LIST_KEY] = cleaned
    selected = state.get(PROFILE_SELECTOR_KEY)
    if selected in cleaned:
        state[PROFILE_KEY] = selected
    elif state.get(PROFILE_KEY) not in cleaned:
        state[PROFILE_KEY] = cleaned[0]
    if state.get(PROFILE_SELECTOR_KEY) not in cleaned:
        state[PROFILE_SELECTOR_KEY] = state[PROFILE_KEY]
    return cleaned


def clear_profile_work(state, auth_key: str | None = None) -> None:
    """Drop transient UI/work state on a profile switch, preserving shared login."""
    preserve = {PROFILE_LIST_KEY, PROFILE_KEY, PROFILE_SELECTOR_KEY, NEW_PROFILE_KEY}
    if auth_key:
        preserve.add(auth_key)
    for key in list(state.keys()):
        if key not in preserve:
            del state[key]


def add_session_profile(state, auth_key: str | None = None, before_switch=None) -> None:
    """Add and activate a name from the profile form's submit callback."""
    names = profile_names(state)
    candidate = str(state.get(NEW_PROFILE_KEY, "")).strip()[:40]
    if not candidate or candidate.casefold() in {item.casefold() for item in names}:
        return
    if before_switch:
        before_switch()
    names.append(candidate)
    state[PROFILE_LIST_KEY] = names
    state[PROFILE_KEY] = candidate
    state[PROFILE_SELECTOR_KEY] = candidate
    state[NEW_PROFILE_KEY] = ""
    clear_profile_work(state, auth_key)


def _logo_data_uri() -> str:
    raw = LOGO_PATH.read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")


def render_header(st, app_name: str, shared: bool, auth_key: str | None = None,
                  before_switch=None) -> str:
    """Render a branded top bar and a session-local, non-authenticating profile picker."""
    names = profile_names(st.session_state)
    active = st.session_state[PROFILE_KEY]

    def switch_profile():
        if before_switch:
            before_switch()
        clear_profile_work(st.session_state, auth_key)

    with st.container(key="gioval-header"):
        left, right = st.columns([5, 2], vertical_alignment="center")
        with left:
            st.markdown(
                '<div class="gioval-topbar-brand">'
                f'<img src="{_logo_data_uri()}" alt="" />'
                '<div class="gioval-topbar-copy"><strong>Gioval</strong>'
                f'<span>{html.escape(app_name)}</span></div>'
                '<span class="gioval-topbar-divider" aria-hidden="true"></span>'
                f'<span class="gioval-topbar-status"><i></i>{"Biblioteca compartida" if shared else "Biblioteca local"}</span>'
                '</div>',
                unsafe_allow_html=True,
            )
        with right:
            with st.popover(f"Perfil · {active}", icon=":material/account_circle:", width="stretch"):
                st.caption("El perfil solo personaliza esta sesión. No es una cuenta ni una autenticación.")
                selected = st.selectbox(
                    "Perfil activo", names, index=names.index(active), key=PROFILE_SELECTOR_KEY,
                    on_change=switch_profile,
                )
                st.session_state[PROFILE_KEY] = selected
                with st.form("gioval_add_profile_form"):
                    st.text_input("Agregar perfil", max_chars=40, key=NEW_PROFILE_KEY,
                                  placeholder="Nombre para el saludo")
                    st.form_submit_button(
                        "Agregar perfil", width="stretch",
                        on_click=add_session_profile,
                        args=(st.session_state, auth_key, before_switch),
                    )
                st.caption(
                    ("El catálogo, el historial y el seguimiento son comunes al espacio de trabajo; "
                     "el perfil solo cambia el saludo de esta sesión." if shared else
                     "El perfil solo cambia el saludo de esta sesión; no crea cuentas ni protege los datos locales.")
                )
    return st.session_state[PROFILE_KEY]


def greeting_tip(name: str, tip: str) -> None:
    """Render a welcome card; names are escaped because this is raw HTML."""
    safe_name = html.escape(name, quote=True)
    safe_tip = html.escape(tip, quote=True)
    import streamlit as st

    st.markdown(
        '<section class="gioval-welcome">'
        f'<div class="gioval-welcome-kicker">Tu espacio Gioval</div>'
        f'<h2>¡Hola, {safe_name}!</h2>'
        f'<p><strong>Dato útil:</strong> {safe_tip}</p></section>',
        unsafe_allow_html=True,
    )
