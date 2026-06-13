"""Configuración central del pipeline saas-radar: env vars + constantes + listas mutables."""

from __future__ import annotations

import os

from dotenv import load_dotenv

# load_dotenv es el único side-effect permitido al importar este módulo.
# Es inofensivo si no existe .env: simplemente no carga nada.
load_dotenv()

# ── Credenciales (env vars) ────────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
REDDIT_CLIENT_ID = (os.getenv("REDDIT_CLIENT_ID") or "").strip()
REDDIT_CLIENT_SECRET = (os.getenv("REDDIT_CLIENT_SECRET") or "").strip()
REDDIT_USER_AGENT = (os.getenv("REDDIT_USER_AGENT") or "saas-radar/1.0").strip()
DB_URL = os.getenv("DB_URL", "sqlite:///data/saas.db")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ALERT_THRESHOLD = int(os.getenv("TELEGRAM_ALERT_THRESHOLD", "8"))

# ── Scraping ───────────────────────────────────────────────────────────────

POST_LIMIT = 100
PAIN_SEARCH_LIMIT = 50
COMMENT_MIN_LENGTH = 50
HIGH_ENGAGEMENT_THRESHOLD = 100
COMMENT_FETCH_WORKERS = 8
# Cap de posts cuyos comentarios se bajan en la fase 3 (se priorizan los de mayor señal)
COMMENT_TARGET_POSTS = 200

# ── AI / LLM ───────────────────────────────────────────────────────────────

# Provider: "claude" (Anthropic), "gemini" (Google AI Studio), "groq" (fallback).
AI_PROVIDER = (os.getenv("AI_PROVIDER") or "claude").lower()

# Provider para la fase de extracción (puede diferir de AI_PROVIDER para evitar rate limits).
# Default "groq": límites generosos en free tier, ideal para los múltiples batches de extracción.
# La síntesis sigue usando AI_PROVIDER.
EXTRACTION_PROVIDER = (os.getenv("EXTRACTION_PROVIDER") or "groq").lower()

# Provider de respaldo cuando la extracción con EXTRACTION_PROVIDER dispara el
# circuit breaker (3 batches consecutivos sin resultado válido). Default "groq"
# porque es el más estable en free tier y rara vez devuelve schemas malformados.
# String vacío → desactiva el fallback (la extracción aborta como antes de F23).
# El fallback se activa una sola vez por run: si también falla, se aborta.
EXTRACTION_PROVIDER_FALLBACK = (os.getenv("EXTRACTION_PROVIDER_FALLBACK") or "groq").lower()

# Claude (Anthropic)
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_EXTRACTION_MODEL = os.getenv("CLAUDE_EXTRACTION_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_SYNTHESIS_MODEL = os.getenv("CLAUDE_SYNTHESIS_MODEL", "claude-sonnet-4-6")

# Gemini (Google AI Studio) — free tier muy generoso: 1500 req/día, 1M TPM
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Groq (legacy / fallback)
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# ── Constantes IA ─────────────────────────────────────────────────────────

# Techo de posts que pasan a la fase de síntesis IA
MAX_POSTS = 80
# Caracteres del body de un post que se envían al LLM por extracción
TEXT_SNIPPET_LEN = 500
# Umbral que debe superar _semantic_score para que un post entre al pipeline de IA
MIN_SEMANTIC_SCORE = 1.0
# Posts más antiguos que esto se descartan
MAX_POST_AGE_DAYS = 365
# En modo incremental: solo posts de las últimas 24h
INCREMENTAL_POST_AGE_DAYS = 1
# Batches consecutivos fallidos antes de abortar la extracción (circuit breaker)
CIRCUIT_BREAKER_THRESHOLD = 3

# Categorías de post que entran al pipeline IA
PAIN_CATEGORIES = ["pain_point", "question_operational"]

# ── Listas mutables ────────────────────────────────────────────────────────

# Subreddits con señal especialmente alta — cap de posts más generoso en el ranking
HIGH_SIGNAL_SUBREDDITS = {
    # IT / infra — alto volumen de workflow pain
    "msp",
    "sysadmin",
    "devops",
    # Finanzas / contabilidad — señal de pago fuerte
    "accounting",
    "bookkeeping",
    "taxpros",
    # Nichos profesionales con dolor operacional concentrado
    "restaurantowners",
    "amazonseller",
    # Agencias / gestión de clientes y reporting
    "agency",
    # Inmobiliario: maintenance / tenants
    "propertymanagement",
    # Construcción: WIP reports, job costing
    "construction",
    # No-code: frustración con límites de tools
    "nocode",
    # Freelance: content creation pain + payment signals
    "freelance",
    # E-commerce: meta run 2026-04-18 → 100% hit, 2 payment signals
    "ecommerce",
    # Small business generalista: meta run 2026-04-18 → 100% hit (4/4), 1 payment
    "smallbusiness",
    # Zapier: meta run 2026-04-18 → 100% hit, 1 payment (tier A herramientas)
    "zapier",
    # Indie hackers: comunidad de fundadores que relatan dolor de producto real (F24, tuner recurrence ≥3)
    "indiehackers",
}

# Posts máximos por subreddit en el ranking final
POSTS_CAP_HIGH_SIGNAL = 15  # cap para HIGH_SIGNAL_SUBREDDITS
POSTS_CAP_DEFAULT = 4  # cap para el resto de subreddits

SUBREDDITS = [
    # ── Tier A — Herramientas concretas ──────────────────────────────────
    # Ratio señal/ruido máximo: la gente viene a resolver un problema específico.
    "zapier",
    "nocode",
    "notion",
    "airtable",
    "googlesheets",
    "excel",
    # ── Tier B — Nichos profesionales ────────────────────────────────────
    # Comunidades donde todos comparten el mismo contexto laboral.
    # Inmobiliario / construcción
    "realtors",
    "PropertyManagement",
    "construction",
    "realestateinvesting",
    # Legal / contabilidad / finanzas
    "Bookkeeping",
    "taxpros",
    "Accounting",
    # Salud
    "veterinaryprofession",
    "physicaltherapy",
    "DentalHygiene",
    # Hostelería / restauración
    "restaurantowners",
    "FoodService",
    # Retail / e-commerce
    "ecommerce",
    "ShopifyeCommerce",
    "AmazonSeller",
    # Agencias / freelance / IT gestionado
    "smallbusiness",
    "freelance",
    "agency",
    "msp",
    # Recruiting / HR
    "recruiting",
    "humanresources",
    # Desarrollo y DevOps — alto volumen de workflow pain
    "devops",
    "sysadmin",
    # ── Tier C — SaaS / tech generalista ─────────────────────────────────
    "microsaas",
    "saas",
    "chrome_extensions",
    # ── Tier D — Aspiracional ────────────────────────────────────────────
    "sideproject",
    "indiehackers",
    "Entrepreneur",
    # r/startups eliminado 2026-04-18: 0/3 hit rate en full-scan (ver README "Tuning")
    # Descubiertos vía pain_search en subreddits no configurados: producen señal real
    "legaladvice",
    "marketing",
    "productivity",
]

PAIN_SEARCH_QUERIES = [
    # ── Workarounds manuales — señal de pago más fuerte ──────────────────
    "I use Excel to track",
    "I use Google Sheets to track",
    "manually copy paste",
    "we do this manually",
    "I hate doing this manually",
    "I export to CSV and then",
    "copy paste every",
    "manually enter",
    "manually update",
    "our agency tracks this in a spreadsheet",
    "we invoice manually because",
    "I built a spreadsheet to track",
    # ── Herramientas concretas con limitaciones ───────────────────────────
    "Zapier doesn't support",
    "Notion doesn't",
    "doesn't integrate with",
    "no integration with",
    "no API for",
    "no webhook",
    # ── Ausencia de herramienta — intención de compra directa ────────────
    "why is there no app for",
    "is there a tool that",
    "looking for a SaaS that",
    "wish someone would build",
    "does anyone know how to automate",
    "looking for software that",
    "looking for alternative to",
    "can't find a tool that",
    # ── Preguntas operacionales genéricas ─────────────────────────────────
    "how do you handle invoicing",
    "how do you track leads",
    "how do you manage scheduling",
    "how do you handle onboarding",
    "how do you track time",
    "how do you manage inventory",
    "how do you handle contracts",
    "how do you manage clients",
    # ── Preguntas operacionales por industria — alta especificidad ────────
    "how do you handle job costing",
    "how do you manage tenant maintenance requests",
    "how do you manage subcontractors",
    "how do you manage client reporting",
    # ── Queries concentradas por nicho ────────────────────────────────────
    # Contabilidad / AR / reconciliación (cluster invoicing)
    "accounts receivable spreadsheet",
    "AR tracking spreadsheet",
    "bank reconciliation takes hours",
    "chasing invoices manually",
    "overdue invoices spreadsheet",
    "manual payment reminders",
    # Restaurantes / food service (cluster tip/payroll/inventory)
    "tip pooling nightmare",
    "tip tracking spreadsheet",
    "food cost spreadsheet",
    "server tip payout",
    "payroll for tipped employees",
    # Property management (cluster maintenance/tenant tracking)
    "maintenance request spreadsheet",
    "tenant maintenance tracking",
    "rent collection spreadsheet",
    "property management spreadsheet",
    "work order tracking manual",
    # Agencias (cluster reporting/client management)
    "client reporting spreadsheet",
    "agency reporting nightmare",
    "agency time tracking spreadsheet",
    "client campaign tracking",
    # MSP / sysadmin (cluster onboarding/remote/docs)
    "onboarding checklist spreadsheet",
    "remote support tool frustration",
    "runbook documentation nightmare",
    # Construcción (cluster WIP/job costing)
    "job costing spreadsheet",
    "WIP report Excel",
    # No-code tool frustration (cluster Softr/Notion limits)
    "Notion limitations business",
]

PAIN_SIGNAL_PHRASES = [
    # Workaround manual explícito — señal de pago más fuerte (+3)
    ("i use excel to", 3),
    ("i use google sheets to", 3),
    ("manually copy", 3),
    ("copy paste", 3),
    ("export to csv", 3),
    ("manually track", 3),
    ("manually enter", 3),
    ("manually update", 3),
    # Herramienta concreta con limitación — muy alta intención de cambio (+3)
    ("zapier doesn't", 3),
    ("zapier can't", 3),
    ("notion doesn't", 3),
    ("notion can't", 3),
    ("airtable doesn't", 3),
    ("quickbooks doesn't", 3),
    ("salesforce is too", 3),
    ("no webhook", 3),
    # Ausencia de herramienta — intención de compra directa (+2)
    ("i wish there was", 2),
    ("wish someone would build", 2),
    ("why is there no", 2),
    ("is there a tool", 2),
    ("looking for a tool", 2),
    ("looking for a saas", 2),
    ("looking for software", 2),
    ("no tool that", 2),
    ("doesn't integrate", 2),
    ("no integration", 2),
    ("no api for", 2),
    # Frustración con precio (+2)
    ("too expensive", 2),
    ("can't afford", 2),
    ("looking for alternative", 2),
    # Pregunta operacional específica (+2)
    ("how do you handle invoic", 2),
    ("how do you manage schedul", 2),
    ("how do you handle onboard", 2),
    ("how do you track time", 2),
    ("how do you manage invent", 2),
    ("how do you handle contra", 2),
    ("how do you manage client", 2),
    # Pregunta operacional genérica (+1)
    ("how do you handle", 1),
    ("how do you manage", 1),
    ("how do you track", 1),
    ("how do you automate", 1),
    ("would pay for", 2),
    # MSP / sysadmin / IT — workarounds físicos o herramientas inadecuadas (+3)
    ("paper timesheets", 3),
    ("faxing", 2),
    ("our techs", 1),
    ("field techs", 1),
    # Contabilidad / AR / facturación específica (+3)
    ("accounts receivable", 2),
    ("chasing invoices", 3),
    ("overdue invoices", 2),
    ("bank reconciliation", 2),
    # E-commerce / fulfillment (+3)
    ("inventory manually", 3),
    # Inmobiliario / property management (+2)
    ("maintenance requests", 2),
    ("rent collection", 2),
    # Workarounds manuales — variantes con spreadsheets y copia manual (+3)
    ("manual process", 3),
    ("doing it by hand", 3),
    ("do it by hand", 3),
    ("track it in a spreadsheet", 3),
    ("keep a spreadsheet", 3),
    ("built a spreadsheet", 3),
    ("use a spreadsheet", 3),
    ("spreadsheet to track", 3),
    ("spreadsheet hell", 3),
    ("drowning in spreadsheets", 3),
    ("google sheet to", 3),
    ("google sheet for", 3),
    ("excel sheet to", 3),
    ("excel to manage", 3),
    ("excel for tracking", 3),
    ("by hand every", 3),
    ("have to copy", 2),
    ("have to paste", 2),
    ("retyping", 3),
    ("re-enter", 3),
    ("double-entry", 3),
    # Dolor temporal explícito (+3) — indica urgencia monetizable
    ("hours a week", 2),
    ("hours per week", 2),
    ("hours a day", 2),
    ("hours every", 2),
    ("takes me hours", 3),
    ("takes hours", 2),
    ("takes forever", 2),
    ("waste of time", 2),
    ("wasting hours", 3),
    # Frustración directa (+2)
    ("is a nightmare", 2),
    ("is a pain", 2),
    ("pain in the ass", 2),
    ("killing me", 2),
    ("driving me crazy", 2),
    ("there has to be a better", 3),
    ("better way to", 2),
    ("must be a better way", 3),
    # Ausencia de tool (+2)
    ("any tool for", 2),
    ("any software for", 2),
    ("any app for", 2),
    ("recommend a tool", 2),
    ("recommend software", 2),
    ("need a tool", 2),
    ("need software", 2),
    ("what do you use for", 1),
    ("what are you using for", 1),
    # Pago / coste explícito (+3)
    ("would pay", 3),
    ("happy to pay", 3),
    ("willing to pay", 3),
    ("per month for", 1),
    ("$ per month", 1),
    # Integraciones rotas (+2)
    ("doesn't connect", 2),
    ("can't connect", 2),
    ("doesn't sync", 2),
    ("breaks when", 2),
    # Operacional (+1)
    ("how are you handling", 2),
    ("how are you tracking", 2),
    ("how are you managing", 2),
    ("any recommendation", 1),
    ("any suggestions", 1),
    # Nuevas frases extraídas de posts oro no capturados (F24)
    ("pdf to csv", 3),
    ("spending too much time", 2),
    ("converting bank statement", 3),
    ("manage inventory in shopify", 2),
]

# Títulos que indican showcase/consejo/historia → descarte inmediato (penalización -99)
SHOWCASE_TITLE_PREFIXES = [
    "how i ",
    "i built",
    "i launched",
    "i made",
    "i created",
    "i shipped",
    "we launched",
    "we built",
    "we shipped",
    "show hn",
    "i quit",
    "left my job",
    "i went from",
    "$",
    "why you should",
    "why you shouldn't",
    "hot take",
    "unpopular opinion",
    "lessons from",
    "what i learned",
    "my journey",
    "after x years",
    "ama:",
    "ama with",
    "promote your",
    "share your",
    "tell me your",
    "looking for feedback",
    "roast my",
    "inspired by",
    "i taught myself",
    "i decided to quit",
    # Consejo personal / narrativa, no pain de negocio
    "am i stupid",
    "am i crazy",
    "am i wrong",
    "my best client",
    # Meta: "sé construir cosas, ¿qué hago?" — no hay pain real
    "i can build",
    "i can code",
    "i can make",
    "degenerate gamblers",  # off-topic literal del subreddit taxpros
    # Showcase disfrazado: "estos son mis sistemas que funcionan"
    "these are the systems",
    "these are my systems",
    "here's what i use",
    "here is what i use",
    "here's my setup",
    "here is my setup",
    "here's how i",
    "here is how i",
    # Consejo / opinión viral genérica
    "advice from",
    "tips from",
    "save yourself",  # autopromoción
    "the best people don't",
    "the best people dont",  # reflexión sin pain
    "you can't scale",
    "you cant scale",  # consejo genérico
    # Opinión generalista con porcentaje
    "90% of you",
    "80% of you",
    "95% of",
    "most of you are",
    # Reflexión sobre trabajo remoto (no pain de herramienta)
    "changed how i see",
    "changed my perspective",
    # Opinión prescriptiva ("deberías hacer X")
    "stop validating",
    "listen to your",
    "the problem isnt",
    "the problem isn't",
    # Opinión / advice meta — no son dolor propio
    "stop coding",
    "stop building",
    "stop trying",
    "your saas",
    "your startup",
    "how to actually",
    "you're building",
    "youre building",
    "you are building",
    "should you build",
    "shouldn't build",
    # Showcase de revenue
    "my saas makes",
    "my saas hit",
    "my startup makes",
    "we hit",
    "we made $",
    "we just hit",
    # Meta-research / agregadores de pain ajeno
    "i documented",
    "i analyzed",
    "i analysed",
    "i researched",
    "i surveyed",
    "i interviewed",
    # Meta-coleccionador: comparte lista de pains ajenos, no tiene pain propio
    "i keep a spreadsheet of every",
    "here are the",
    "the biggest pain",
    "top niches",
]

# Frases que indican contenido off-topic → descarte inmediato (penalización -50)
OFF_TOPIC_SIGNALS = [
    # Burnout / salud mental
    "burned out",
    "burnt out",
    "mental health",
    "anxiety",
    "depression",
    "therapist",
    "therapy",
    "dopamine",
    "burnout",
    "suicidal",
    "panic attack",
    "mental breakdown",
    # Política / religión
    "political",
    "politics",
    "republican",
    "democrat",
    "trump",
    "election",
    "religion",
    "religious",
    "abortion",
    # Contenido motivacional sin problema de negocio
    "you are not lazy",
    "you're not lazy",
    "dopamine-depleted",
    "wake up at 5",
    "morning routine",
    "cold shower",
    # Contratación / carrera personal
    "got fired",
    "laid off",
    "salary negotiation",
    "interview tips",
    "linkedin profile",
    # Posts meta — análisis de otros, no dolor propio
    "i analyzed",
    "i analysed",
    "here are the top",
    "biggest pain points are",
    "best niches",
    "i researched",
    "after analyzing",
    "i surveyed",
    "i interviewed",
    "according to my research",
    # Opinión / advice genérico sin pain propio
    "nobody wants",
    "shouldn't exist",
    "should not exist",
    "probably shouldn't",
    "you should not build",
    "you shouldn't build",
    "stop building",
    "stop coding",
    # Macro / política comercial — ruido para SaaS micro
    "tariff",
    "tariffs",
    # Educación / carrera personal (no business pain)
    "is it worth it to go",
    "worth going to",
    "should i go to school",
    "0 debt going",
    "0 debt goin",  # variante coloquial sin g
    "going into pt",
    "goin into pt",
    "going into med",
    "goin into med",
    # Showcase de adquisición / "growth hack"
    "to get my first 50",
    "to get my first 100",
    "to get my first paying",
    "first 50 customers",
    "first 100 customers",
    # Off-topic temáticos
    "gambling",
    "degenerate gambler",
    # Showcase disfrazado en mitad de título
    "these are the systems that",
    "these are my systems",
    "here are my systems",
]
