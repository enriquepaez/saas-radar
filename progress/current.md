# Sesión actual

> Este archivo se vacía al cerrar cada sesión y se mueve a `history.md`.
> Mientras trabajas, **mantenlo actualizado en tiempo real**, no al final.

- **Feature en curso:** split_extraction_provider — Provider separado para extracción vs síntesis
- **Inicio:** 2026-05-31
- **Agente:** implementer

## Plan

- Añadir `EXTRACTION_PROVIDER` en `config.py` justo después de `AI_PROVIDER`.
- En `ai_analyzer.py` cambiar las llamadas de extracción para usar `config.EXTRACTION_PROVIDER` en lugar del `provider` original.
- Añadir `EXTRACTION_PROVIDER` al bloque `env:` de `.github/workflows/pipeline.yml`.
- Añadir test en `tests/test_ai_analyzer.py` que verifica que la extracción usa `config.EXTRACTION_PROVIDER` aunque `AI_PROVIDER` sea distinto.
- Ejecutar `./init.sh` y verificar que todo está verde.

## Bitácora

- Leyendo archivos de contexto: AGENTS.md, feature_list.json, config.py, ai_analyzer.py, pipeline.yml, tests.
- Implementando cambios.

## Próximo paso

Implementar los 4 cambios en orden.
