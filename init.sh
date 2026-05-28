#!/usr/bin/env bash
# init.sh — Verificación e inicialización del entorno
#
# Este script lo ejecuta el agente al COMENZAR una sesión y antes de
# declarar cualquier tarea como `done`. Si falla, la sesión no debe avanzar.
#
# Salida esperada: códigos de salida claros y bloques marcados con [OK]/[FAIL].

set -u
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$1"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
fail()  { printf "${RED}[FAIL]${NC}  %s\n" "$1"; }

EXIT_CODE=0

echo "── 1. Verificando entorno ─────────────────────────────"

# Python disponible
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 no está instalado"
  exit 1
fi
ok "python3 -> $(python3 --version)"

# Versión mínima 3.11 (el legacy usa 3.11 en CI y stack moderno)
PY_VERSION_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))')
if [ "$PY_VERSION_OK" != "1" ]; then
  fail "Se requiere Python >= 3.11"
  exit 1
fi
ok "Versión de Python compatible (>= 3.11)"

echo ""
echo "── 2. Verificando archivos base del arnés ──────────────"

for f in \
  AGENTS.md \
  feature_list.json \
  progress/current.md \
  docs/architecture.md \
  docs/conventions.md \
  docs/verification.md \
  CHECKPOINTS.md \
  docs/legacy-context/inventory.md \
  docs/legacy-context/architecture.md \
  docs/legacy-context/lessons-learned.md \
  docs/legacy-context/feature-backlog.md
do
  if [ ! -f "$f" ]; then
    fail "Falta archivo base: $f"
    EXIT_CODE=1
  else
    ok "Existe $f"
  fi
done

echo ""
echo "── 3. Validando feature_list.json ──────────────────────"

python3 - <<'PY'
import json, sys
try:
    data = json.load(open("feature_list.json"))
    valid = {"pending", "in_progress", "done", "blocked"}
    in_progress = [f for f in data["features"] if f["status"] == "in_progress"]
    if len(in_progress) > 1:
        print(f"[FAIL]  Hay {len(in_progress)} features en in_progress (máximo 1)")
        sys.exit(1)
    ids_seen = set()
    ids_all = {f["id"] for f in data["features"]}
    for f in data["features"]:
        if f["status"] not in valid:
            print(f"[FAIL]  Estado inválido en feature {f['id']}: {f['status']}")
            sys.exit(1)
        if f["id"] in ids_seen:
            print(f"[FAIL]  id duplicado: {f['id']}")
            sys.exit(1)
        ids_seen.add(f["id"])
        # Validar dependencias
        for dep in f.get("depends_on", []):
            if dep not in ids_all:
                print(f"[FAIL]  feature {f['id']} declara dep en id {dep} inexistente")
                sys.exit(1)
    # Coherencia: ninguna done depende de pending/blocked
    by_id = {f["id"]: f for f in data["features"]}
    for f in data["features"]:
        if f["status"] != "done":
            continue
        for dep in f.get("depends_on", []):
            if by_id[dep]["status"] != "done":
                print(f"[FAIL]  feature {f['id']} done pero depende de {dep} ({by_id[dep]['status']})")
                sys.exit(1)
    print(f"[OK]    feature_list.json válido ({len(data['features'])} features)")
except Exception as e:
    print(f"[FAIL]  feature_list.json inválido: {e}")
    sys.exit(1)
PY

if [ $? -ne 0 ]; then EXIT_CODE=1; fi

echo ""
echo "── 4. Verificando paquete src/saas_radar ──────────────"

if [ -f "pyproject.toml" ]; then
  ok "pyproject.toml existe"
  if [ -d "src/saas_radar" ]; then
    ok "src/saas_radar/ existe"
  else
    warn "src/saas_radar/ no existe todavía (lo crea feature #1)"
  fi
else
  warn "pyproject.toml no existe todavía (lo crea feature #1)"
fi

echo ""
echo "── 5. Ejecutando tests ─────────────────────────────────"

if [ -d "tests" ] && [ -n "$(find tests -name 'test_*.py' -print -quit 2>/dev/null)" ]; then
  if command -v pytest >/dev/null 2>&1; then
    if python3 -m pytest -q 2>&1; then
      ok "Todos los tests pasan"
    else
      fail "Hay tests rotos"
      EXIT_CODE=1
    fi
  else
    warn "pytest no instalado todavía. Tras feature #1: pip install -e ."
  fi
else
  warn "Carpeta tests/ sin tests aún"
fi

echo ""
echo "── 6. Verificando anti-patrones del legacy ────────────"

if [ -d "src" ]; then
  hits=$(grep -rn "sys.path.append" src/ 2>/dev/null | wc -l)
  if [ "$hits" -gt 0 ]; then
    fail "Detectado sys.path.append en src/ ($hits ocurrencias) — anti-patrón del legacy (ver docs/legacy-context/lessons-learned.md §2.4)"
    grep -rn "sys.path.append" src/ 2>/dev/null
    EXIT_CODE=1
  else
    ok "Sin sys.path.append en src/"
  fi
else
  warn "src/ no existe aún — verificación de anti-patrones aplazada"
fi

echo ""
echo "── 7. Resumen ──────────────────────────────────────────"

if [ $EXIT_CODE -eq 0 ]; then
  ok "Entorno listo. Puedes empezar a trabajar."
else
  fail "Entorno NO está listo. Resuelve los errores antes de avanzar."
fi

exit $EXIT_CODE
