"""Prompt builder para el agente GTM (Go-to-Market)."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def build_gtm_prompt(opp: dict) -> str:
    """Construye el prompt GTM para una oportunidad de micro-SaaS.

    Recibe el dict de una oportunidad (columnas de la tabla opportunities)
    e incluye hasta 5 evidence_quotes en el prompt.

    El prompt instruye al LLM para que devuelva un único JSON con 3 bloques:
    1. Viabilidad (3 puntuaciones 0-10).
    2. GTM (elevator pitch, pricing, canales, scripts).
    3. Plan 7 días (plan diario, señales de pivote, KPIs).
    """
    product_name = opp.get("product_name") or "Producto sin nombre"
    core_problem = opp.get("core_problem") or ""
    niche = opp.get("niche") or ""
    why_gap_exists = opp.get("why_gap_exists") or ""
    concrete_workaround = opp.get("concrete_workaround") or ""
    workaround_cost = opp.get("workaround_cost") or ""
    mvp_scope = opp.get("mvp_scope") or ""
    estimated_price = opp.get("estimated_price") or ""
    monetization = opp.get("monetization") or ""
    competitor_gap = opp.get("competitor_gap") or ""
    priority_score = opp.get("priority_score") or 0

    # Parsear evidence_quotes con tolerancia: puede ser JSON array o string plano.
    evidence_quotes_raw = opp.get("evidence_quotes")
    quotes: list[str] = []
    if evidence_quotes_raw:
        if isinstance(evidence_quotes_raw, list):
            quotes = evidence_quotes_raw
        elif isinstance(evidence_quotes_raw, str):
            try:
                parsed = json.loads(evidence_quotes_raw)
                if isinstance(parsed, list):
                    quotes = [str(q) for q in parsed]
                else:
                    quotes = [str(parsed)]
            except json.JSONDecodeError:
                # Si no es JSON válido, tratar el string como una sola cita.
                quotes = [evidence_quotes_raw]

    # Tomar máximo 5 citas para no sobrecargar el contexto del LLM.
    quotes = quotes[:5]
    quotes_block = "\n".join(f'  - "{q}"' for q in quotes) if quotes else "  (sin citas disponibles)"

    prompt = f"""Eres un experto en estrategia de micro-SaaS y Go-to-Market (GTM). \
Analiza la siguiente oportunidad de negocio detectada en Reddit y devuelve \
EXCLUSIVAMENTE un JSON con la estructura exacta indicada más abajo. \
Sin texto adicional, sin explicaciones, sin markdown fuera del JSON.

## OPORTUNIDAD A ANALIZAR

- **Producto**: {product_name}
- **Nicho**: {niche}
- **Problema core**: {core_problem}
- **Por qué existe el gap**: {why_gap_exists}
- **Workaround actual**: {concrete_workaround}
- **Coste del workaround**: {workaround_cost}
- **Scope del MVP**: {mvp_scope}
- **Precio estimado**: {estimated_price}
- **Modelo de monetización**: {monetization}
- **Gap vs competidores**: {competitor_gap}
- **Priority score**: {priority_score}/10

### Citas de evidencia de usuarios reales (Reddit):
{quotes_block}

---

## TAREA 1: VIABILIDAD

Puntúa de 0 a 10 (entero) cada dimensión:
- `viability_desperation`: urgencia del dolor del usuario (10 = crítico, 0 = trivial).
- `viability_build_ease`: facilidad de construir el MVP en solitario (10 = muy fácil, 0 = muy complejo).
- `viability_scalability`: escalabilidad del modelo de negocio (10 = muy escalable, 0 = nicho muy pequeño).

## TAREA 2: GO-TO-MARKET

- `elevator_pitch`: una sola frase (≤20 palabras) que describa el producto y su propuesta de valor.
- `pricing_tiers`: lista de 2-3 planes de precio. Cada plan: {{"name": str, "price": str, "features": [str]}}.
- `acquisition_channels`: lista de 2-3 canales de adquisición (EXCLUIR Reddit como canal). \
Cada canal: {{"platform": str, "tactic": str, "cost_estimate": str}}.
- `cold_outreach_script`: mensaje de prospección en frío (≤80 palabras, directo y personal).
- `organic_post_template`: plantilla de post orgánico para captar early adopters (≤120 palabras).

## TAREA 3: PLAN 7 DÍAS

- `validation_plan_7d`: lista de 7 objetos (uno por día). Cada objeto: \
{{"day": int, "action": str, "success_criterion": str}}.
- `pivot_signals`: lista de 3-5 strings. Cada string es una señal que indicaría que hay que pivotar.
- `kpis`: objeto con 3 métricas target para el mes 3: \
{{"cac_target": str, "activation_target": str, "mrr_target_m3": str}}.

---

## FORMATO DE RESPUESTA (JSON estricto, sin texto adicional)

{{
  "viability_desperation": <int 0-10>,
  "viability_build_ease": <int 0-10>,
  "viability_scalability": <int 0-10>,
  "elevator_pitch": "<str>",
  "pricing_tiers": [
    {{"name": "<str>", "price": "<str>", "features": ["<str>"]}}
  ],
  "acquisition_channels": [
    {{"platform": "<str>", "tactic": "<str>", "cost_estimate": "<str>"}}
  ],
  "cold_outreach_script": "<str ≤80 palabras>",
  "organic_post_template": "<str ≤120 palabras>",
  "validation_plan_7d": [
    {{"day": 1, "action": "<str>", "success_criterion": "<str>"}}
  ],
  "pivot_signals": ["<str>"],
  "kpis": {{
    "cac_target": "<str>",
    "activation_target": "<str>",
    "mrr_target_m3": "<str>"
  }}
}}
"""
    return prompt
