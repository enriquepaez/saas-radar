"""Tests de phase_gtm() y su integración con el pipeline principal."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from saas_radar.main import phase_gtm


def test_phase_gtm_catches_exception(capsys):
    """Si run_all_pending lanza excepción, phase_gtm no propaga (solo imprime WARN)."""
    with patch("saas_radar.agents.gtm_agent.run_all_pending", side_effect=RuntimeError("boom")):
        # No debe levantar excepción.
        phase_gtm()

    captured = capsys.readouterr()
    assert "[WARN]" in captured.out
    assert "boom" in captured.out


def test_phase_gtm_prints_result_summary(capsys):
    """Con mock de run_all_pending retornando dict, imprime los 4 conteos."""
    mock_result = {"generated": 3, "skipped_low_viability": 1, "failed": 0, "skipped_existing": 2}

    with patch("saas_radar.agents.gtm_agent.run_all_pending", return_value=mock_result):
        phase_gtm()

    captured = capsys.readouterr()
    assert "3 generadas" in captured.out
    assert "1 baja viabilidad" in captured.out
    assert "0 fallidas" in captured.out
    assert "2 ya existentes" in captured.out


def test_phase_gtm_calls_run_all_pending(capsys):
    """phase_gtm llama a run_all_pending sin parámetro provider (siempre groq)."""
    captured_calls = []

    def mock_run_all_pending(**kwargs):
        captured_calls.append(kwargs)
        return {"generated": 1, "skipped_low_viability": 0, "failed": 0, "skipped_existing": 0}

    with patch("saas_radar.agents.gtm_agent.run_all_pending", side_effect=mock_run_all_pending):
        phase_gtm()

    assert len(captured_calls) == 1
    # provider ya no es un parámetro aceptado
    assert "provider" not in captured_calls[0]


def test_skip_gtm_flag_does_not_import_agent(tmp_path):
    """Con --skip-gtm, el pipeline imprime el mensaje de omisión sin importar el agente."""
    # Usamos run_pipeline con skip_scrape + skip_ai + skip_gtm para hacer un run rápido.
    # Mockeamos init_db y has_successful_run para evitar tocar la BD real.
    with (
        patch("saas_radar.main.init_db"),
        patch("saas_radar.main.has_successful_run", return_value=True),
        patch("saas_radar.main.phase_subreddits"),
        patch("saas_radar.main.phase_pain_search"),
        patch("saas_radar.main.phase_comments"),
        patch("saas_radar.main.run_ai_analysis"),
    ):
        # Importamos run_pipeline con skip_gtm=True
        from saas_radar.main import run_pipeline
        import io
        import sys

        captured = io.StringIO()
        sys.stdout = captured
        try:
            run_pipeline(skip_scrape=True, skip_ai=True, skip_gtm=True)
        finally:
            sys.stdout = sys.__stdout__

        output = captured.getvalue()

    # Debe imprimir mensaje de omisión, NO el encabezado de FASE 5 con resultados GTM
    assert "GTM agent omitido" in output
    # No debe haber llamado a la fase GTM completa
    assert "GTM agent falló" not in output
