"""Presentación de pesos chilenos, manteniendo valores numéricos en el modelo."""
import math
import re
import pandas as pd
import streamlit as st


def clp(valor):
    if valor is None or pd.isna(valor):
        return ""
    return "$" + format(round(float(valor)), ",").replace(",", ".")


def leer(valor):
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return None
    if isinstance(valor, str):
        texto = valor.strip().removeprefix("$").strip()
        if not texto:
            return None
        if not re.fullmatch(r"-?(?:\d{1,3}(?:\.\d{3})+|\d+)", texto):
            raise ValueError("Usa pesos enteros, por ejemplo $1.234.567.")
        return int(texto.replace(".", ""))
    if not math.isfinite(float(valor)):
        raise ValueError("El monto no es válido.")
    return round(float(valor))


def preparar(datos, kw):
    config = dict(kw.get("column_config") or {})
    columnas = [c for c, v in config.items() if isinstance(v, dict)
                and str(v.get("type_config", {}).get("format", "")).startswith("$")]
    df = datos.copy() if isinstance(datos, pd.DataFrame) else pd.DataFrame(datos)
    columnas = [c for c in columnas if c in df.columns]
    for c in columnas:
        df[c] = df[c].map(clp)
        anterior = config[c]
        config[c] = st.column_config.TextColumn(
            label=anterior.get("label"), width=anterior.get("width"),
            help=anterior.get("help") or "Pesos chilenos sin decimales. Ejemplo: $1.234.567",
            disabled=anterior.get("disabled"),
            validate=r"^\s*\$?\s*-?(?:\d{1,3}(?:\.\d{3})+|\d+)?\s*$",
        )
    return df, {**kw, "column_config": config}, columnas


def tabla(datos, **kw):
    df, opciones, _ = preparar(datos, kw)
    return st.dataframe(df, **opciones)


def editor(datos, **kw):
    df, opciones, columnas = preparar(datos, kw)
    if columnas and opciones.get("key"):
        opciones["key"] += "__clp"
    editado = st.data_editor(df, **opciones)
    for c in columnas:
        # Preserve underlying precision when the displayed amount was not edited.
        valores = []
        for pos, valor in enumerate(editado[c]):
            if pos < len(df) and valor == df[c].iloc[pos]:
                valores.append(datos[c].iloc[pos])
            else:
                numero = leer(valor)
                minimo = kw["column_config"][c].get("type_config", {}).get("min_value")
                if numero is not None and minimo is not None and numero < minimo:
                    st.error(f"El monto de {c} debe ser al menos {clp(minimo)}.")
                    st.stop()
                valores.append(numero)
        editado[c] = pd.Series(valores, index=editado.index, dtype="float64")
    return editado


def entrada(label, *, value=0, key=None, min_value=0, on_change=None, args=(), _container=st, **kw):
    clave = key or "monto_" + label
    texto_clave = clave + "__texto_clp"
    st.session_state.setdefault(clave, value)
    st.session_state.setdefault(texto_clave, clp(st.session_state[clave]))

    def confirmar():
        try:
            numero = leer(st.session_state[texto_clave])
            if numero is None or numero < min_value:
                raise ValueError("Revisa el monto ingresado.")
            st.session_state[clave] = numero
            st.session_state[texto_clave] = clp(numero)
            if on_change:
                on_change(*args)
        except ValueError:
            pass

    for opcion in ("step", "format", "max_value"):
        kw.pop(opcion, None)
    _container.text_input(label, key=texto_clave, on_change=confirmar, **kw)
    try:
        numero = leer(st.session_state[texto_clave])
        if numero is None or numero < min_value:
            raise ValueError("Revisa el monto ingresado.")
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    return st.session_state[clave]
