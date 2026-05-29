"""Limpieza NLP de texto: eliminación de URLs, puntuación y stopwords (EN + ES)."""

from __future__ import annotations

import re

import nltk
from nltk.corpus import stopwords

# Descarga con cache: si ya están descargadas, quiet=True evita output y no re-descarga.
nltk.download("stopwords", quiet=True)

# Stopwords en inglés y español combinadas en un único set, calculadas UNA vez al importar.
# Usar set (tabla hash) hace el lookup O(1) por palabra, mucho más rápido que list.
_STOP_WORDS: set[str] = set(stopwords.words("english")) | set(stopwords.words("spanish"))

# Regex compiladas UNA vez al importar. Compilar = pre-construir el autómata finito que
# evalúa el patrón; hacerlo al import evita re-compilar en cada llamada a clean_text.
#
# _RE_URL: elimina URLs completas. \S+ captura cualquier secuencia sin espacio
#   → cubre http://x.com, https://x.com/path?q=1#fragment, etc.
_RE_URL: re.Pattern[str] = re.compile(r"https?://\S+", re.IGNORECASE)

# _RE_NOT_ALPHA: elimina todo lo que NO sea letra a-z o espacio.
# [^a-z\s] → complemento del conjunto: dígitos, puntuación, emojis, etc.
# Se aplica DESPUÉS de bajar a minúsculas, por eso solo [a-z].
_RE_NOT_ALPHA: re.Pattern[str] = re.compile(r"[^a-z\s]")

# _RE_MULTI_SPACE: colapsa múltiples espacios/tabs/newlines en un solo espacio.
_RE_MULTI_SPACE: re.Pattern[str] = re.compile(r"\s+")

# _RE_URL_NORMALIZE: para normalize_for_classifier, que solo elimina URLs pero
# preserva puntuación, dígitos y $. Mismo patrón que _RE_URL.
_RE_URL_NORMALIZE: re.Pattern[str] = re.compile(r"https?://\S+", re.IGNORECASE)


def clean_text(text: str) -> str:
    """Limpia texto para análisis NLP.

    Pasos (en orden):
    1. Guard: devuelve "" si la entrada no es string o está vacía.
    2. Lowercase — normaliza mayúsculas para que "Excel" == "excel".
    3. Elimina URLs (http/https) — ruido sin señal semántica.
    4. Elimina todo lo que no sea letra a-z o espacio — puntuación, emojis, dígitos.
    5. Filtra stopwords EN+ES y palabras de 1-2 chars — "a", "de", "the" son ruido.
    6. Une las palabras supervivientes con un espacio.

    Args:
        text: Texto crudo del post o comentario.

    Returns:
        String limpio con solo palabras de contenido en minúsculas, sin URL ni
        stopwords ni puntuación. Puede ser "" si la entrada estaba vacía o era
        solo stopwords.
    """
    if not isinstance(text, str) or not text.strip():
        return ""

    # Paso 2: lowercase. El cast .lower() devuelve una nueva str; no muta.
    text = text.lower()

    # Paso 3: eliminar URLs. re.sub reemplaza cada match de _RE_URL por "".
    text = _RE_URL.sub("", text)

    # Paso 4: eliminar caracteres no alfabéticos. Después del lowercase, solo
    # quedan a-z, así que [^a-z\s] elimina todo lo demás (puntuación, dígitos,
    # emojis representados como caracteres unicode fuera del rango a-z, etc.).
    text = _RE_NOT_ALPHA.sub("", text)

    # Paso 4b: colapsar espacios múltiples que quedan tras las sustituciones.
    text = _RE_MULTI_SPACE.sub(" ", text).strip()

    # Paso 5: filtrar stopwords y palabras muy cortas.
    # w not in _STOP_WORDS: lookup O(1) en el set unificado EN+ES.
    # len(w) > 2: elimina "a", "de", "el", "al", etc. que puedan escapar el set.
    words = [w for w in text.split() if w not in _STOP_WORDS and len(w) > 2]

    # Paso 6: reconstruir string.
    return " ".join(words)


def normalize_for_classifier(text: str) -> str:
    """Normalización mínima para el clasificador.

    A diferencia de clean_text, NO elimina puntuación, dígitos ni stopwords:
    el clasificador necesita "?" para detectar preguntas, "$" para detectar
    showcases, y "how" / "anyone" para detectar tipos de pregunta.

    Pasos:
    1. Guard: devuelve "" si la entrada no es string.
    2. Elimina URLs (contienen ruido sin señal para el clasificador).
    3. Lowercase + strip.

    Args:
        text: Texto crudo (título o body del post).

    Returns:
        String en minúsculas sin URLs. Conserva puntuación, dígitos y stopwords.
    """
    if not isinstance(text, str):
        return ""

    # Elimina URLs antes del lowercase: el patrón es case-insensitive, pero
    # hacerlo aquí evita que "HTTP://X.COM" sobreviva al paso siguiente.
    text = _RE_URL_NORMALIZE.sub("", text)

    return text.lower().strip()
