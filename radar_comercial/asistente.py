"""Ayuda contextual para Radar Comercial Gioval."""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Any

MODELO_POR_DEFECTO = "gpt-6-luna"
MAX_PREGUNTA = 1200
MAX_HISTORIAL = 8

GUIAS_RAPIDAS = {
    "Primeros pasos": "Revisa el Panel comercial, busca o importa procesos en Radar de oportunidades, evalúa compatibilidad y margen, y guarda la evaluación compartida. Cambiar de sección no guarda el borrador.",
    "Buscar oportunidades": "En Radar de oportunidades usa una palabra por línea, elige región y estado y pulsa Buscar. Máximo ocho términos distintos. Necesitas un ticket oficial; sin ticket puedes importar Excel/CSV.",
    "Importar Excel o CSV": "Selecciona XLSX o CSV en Radar de oportunidades y pulsa Importar archivo seleccionado. Revisa las columnas detectadas; puedes añadir manualmente las oportunidades que falten.",
    "Evaluar rentabilidad": "Revisa los requisitos, selecciona un producto de catálogo y confirma manualmente modelo, presentación, unidad y compatibilidad. Ingresa el precio de oferta neto y los costos adicionales; pulsa Guardar evaluación compartida.",
    "IVA, costos y margen": "Por defecto los importes del catálogo incluyen IVA y se normalizan a netos para el cálculo. La oferta y costos adicionales se introducen netos; el margen es sobre la venta neta. Verifica qué incluye el presupuesto publicado.",
    "Seguimiento y datos": "Usa Seguimiento para revisar estados y evaluaciones. El Radar guarda sus propios datos en radar_comercial.db y no modifica el historial del cotizador.",
}


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def buscar_api_key(carpeta_app: str | Path) -> tuple[str, str]:
    """Busca una clave de API, sin almacenarla."""
    clave = os.environ.get("OPENAI_API_KEY", "").strip()
    if clave:
        return clave, "variable de entorno"
    try:
        lineas = (Path(carpeta_app) / ".env").read_text("utf-8", errors="ignore").splitlines()
    except OSError:
        return "", "sin configurar"
    for linea in lineas:
        limpia = linea.strip()
        if limpia.startswith("export "):
            limpia = limpia[7:].lstrip()
        nombre, sep, valor = limpia.partition("=")
        if sep and nombre.strip() == "OPENAI_API_KEY":
            valor = valor.split(" #", 1)[0].strip().strip("'\"")
            if valor:
                return valor, "archivo .env"
    return "", "sin configurar"


def responder_local(pregunta: str, vista_actual: str = "") -> str:
    q = _normalizar(pregunta)
    if any(k in q for k in ("margen", "rentabilidad", "costo", "precio", "iva", "beneficio")):
        if "incompleto" in q or "no aparece" in q or "no calcula" in q:
            return (
                "Un margen puede salir incompleto si faltan datos que el Radar exige para no crear una cifra engañosa: "
                "confirma manualmente la equivalencia de cada producto, verifica que el costo del catálogo exista, "
                "ingresa precio y costos adicionales en valores netos y confirma que despacho/otros gastos están revisados "
                "(pueden ser $0). Luego revisa que la línea esté incluida y vuelve a mirar el resultado."
            )
        return GUIAS_RAPIDAS["IVA, costos y margen"]
    if any(k in q for k in ("import", "excel", "csv", "archivo", "subir")):
        return GUIAS_RAPIDAS["Importar Excel o CSV"]
    if any(k in q for k in ("buscar", "compra agil", "chilecompra", "ticket", "api", "oportunidad")):
        return GUIAS_RAPIDAS["Buscar oportunidades"]
    if any(k in q for k in ("evalu", "producto", "equivalencia", "requisito", "cotizar")):
        return GUIAS_RAPIDAS["Evaluar rentabilidad"]
    if any(k in q for k in ("seguimiento", "guardar", "datos", "respaldo", "base", "historial")):
        return GUIAS_RAPIDAS["Seguimiento y datos"]
    if any(k in q for k in ("empez", "primero", "iniciar", "uso", "ayuda", "naveg")):
        return GUIAS_RAPIDAS["Primeros pasos"]
    return (f"Estás en {vista_actual}. " if vista_actual else "") + "La guía local puede explicar pasos frecuentes. Para preguntas libres, configura OPENAI_API_KEY o pega una clave en Ayuda / IA."


def instrucciones_ia(vista_actual: str) -> str:
    return (
        "Eres el asistente integrado para explicar el uso de Radar Comercial Gioval. Responde en español, paso a paso. "
        f"Sección actual: {vista_actual}. La aplicación tiene Panel comercial, Radar de oportunidades, Evaluar rentabilidad y Seguimiento. "
        "La API de Compra Ágil necesita un ticket oficial y respeta su cuota; también es posible importar Excel/CSV. "
        "El catálogo se toma por defecto con IVA incluido y el cálculo se realiza en neto. La equivalencia de productos requiere confirmación manual; "
        "el precio de oferta y gastos se ingresan netos; se debe pulsar Guardar evaluación compartida para guardar. "
        "La app no presenta ofertas ni modifica el historial del cotizador. No puedes ver bases, archivos, credenciales ni datos de procesos en tiempo real; "
        "solo recibes el mensaje y la conversación reciente. No pidas ni expongas tickets, contraseñas, RUT ni datos personales. "
        "No afirmes que ejecutas acciones ni inventes controles; cuando no estés seguro, dilo y pide verificar en la interfaz. "
        "Guías de referencia para responder con precisión:\n"
        + "\n".join(f"{k}: {v}" for k, v in GUIAS_RAPIDAS.items())
    )


def responder_con_ia(pregunta: str, vista_actual: str, api_key: str, historial: list[dict[str, str]] | None = None, cliente: Any = None, modelo: str | None = None) -> str:
    """Responde con el modelo; nunca adjunta catálogo, bases de datos o tickets."""
    if not str(api_key or "").strip():
        raise ValueError("No se configuró OPENAI_API_KEY.")
    pregunta = str(pregunta or "").strip()[:MAX_PREGUNTA]
    if not pregunta:
        raise ValueError("Escribe una pregunta.")
    if cliente is None:
        from openai import OpenAI
        cliente = OpenAI(api_key=api_key.strip(), timeout=25.0, max_retries=1)
    entradas = []
    for item in (historial or [])[-MAX_HISTORIAL:]:
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and item.get("content"):
            entradas.append({"role": item["role"], "content": str(item["content"])[:MAX_PREGUNTA]})
    entradas.append({"role": "user", "content": pregunta})
    modelo_final = (modelo or os.environ.get("GIOVAL_RADAR_AI_MODEL") or MODELO_POR_DEFECTO).strip()
    respuesta = cliente.responses.create(model=modelo_final, instructions=instrucciones_ia(vista_actual), input=entradas, max_output_tokens=500, store=False)
    texto = str(getattr(respuesta, "output_text", "") or "").strip()
    if not texto:
        raise RuntimeError("El modelo no devolvió texto; intenta otra formulación.")
    return texto
