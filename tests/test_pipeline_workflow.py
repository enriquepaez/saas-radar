"""Tests de validación del workflow GitHub Actions pipeline.yml."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "pipeline.yml"
TUNER_WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "tuner.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    """Carga y parsea el workflow YAML una sola vez por módulo."""
    assert WORKFLOW_PATH.exists(), f"El workflow no existe en {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def tuner_workflow() -> dict:
    """Carga y parsea el workflow del tuner una sola vez por módulo."""
    assert TUNER_WORKFLOW_PATH.exists(), f"El workflow no existe en {TUNER_WORKFLOW_PATH}"
    with TUNER_WORKFLOW_PATH.open() as f:
        return yaml.safe_load(f)


def test_workflow_file_exists_and_is_valid_yaml():
    """El archivo .github/workflows/pipeline.yml existe y es YAML válido."""
    assert WORKFLOW_PATH.exists(), f"Falta {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open() as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "El YAML debe ser un mapeo raíz"


def test_has_cron_schedule(workflow: dict):
    """El workflow tiene trigger schedule con cron '0 8 * * *' (diario a las 8 UTC)."""
    on_block = workflow.get("on", {})
    assert "schedule" in on_block, "Falta trigger 'schedule'"
    crons = [entry.get("cron") for entry in on_block["schedule"]]
    assert "0 8 * * *" in crons, f"Cron '0 8 * * *' no encontrado. Crons: {crons}"


def test_has_workflow_dispatch_with_full_scan(workflow: dict):
    """El workflow tiene workflow_dispatch con input full_scan de tipo boolean."""
    on_block = workflow.get("on", {})
    assert "workflow_dispatch" in on_block, "Falta trigger 'workflow_dispatch'"
    dispatch = on_block["workflow_dispatch"]
    assert dispatch is not None, "'workflow_dispatch' no puede estar vacío"
    inputs = dispatch.get("inputs", {})
    assert "full_scan" in inputs, "Falta input 'full_scan' en workflow_dispatch"
    full_scan_input = inputs["full_scan"]
    assert full_scan_input.get("type") == "boolean", (
        f"Input full_scan debe ser tipo boolean, es: {full_scan_input.get('type')}"
    )


def test_has_concurrency_config(workflow: dict):
    """El workflow tiene concurrency group 'saas-radar' con cancel-in-progress: false."""
    concurrency = workflow.get("concurrency")
    assert concurrency is not None, "Falta bloque 'concurrency'"
    assert concurrency.get("group") == "saas-radar", (
        f"concurrency.group debe ser 'saas-radar', es: {concurrency.get('group')}"
    )
    assert concurrency.get("cancel-in-progress") is False, (
        f"cancel-in-progress debe ser false, es: {concurrency.get('cancel-in-progress')}"
    )


def test_has_cache_restore_step(workflow: dict):
    """El job 'run' tiene un step que usa actions/cache@v4 con path: data/saas.db."""
    steps = workflow["jobs"]["run"]["steps"]
    cache_steps = [s for s in steps if s.get("uses", "").startswith("actions/cache")]
    assert len(cache_steps) >= 1, "Falta step con actions/cache"
    cache_step = cache_steps[0]
    assert cache_step.get("with", {}).get("path") == "data/saas.db", (
        "El step de cache debe tener path: data/saas.db"
    )


def test_cache_key_uses_run_id(workflow: dict):
    """El cache key contiene github.run_id y restore-keys contiene 'saas-db-'."""
    steps = workflow["jobs"]["run"]["steps"]
    cache_steps = [s for s in steps if s.get("uses", "").startswith("actions/cache")]
    assert len(cache_steps) >= 1, "Falta step con actions/cache"
    cache_step = cache_steps[0]
    with_block = cache_step.get("with", {})
    key = with_block.get("key", "")
    assert "run_id" in key, f"La cache key debe contener 'run_id', es: {key}"
    restore_keys = with_block.get("restore-keys", "")
    assert "saas-db-" in restore_keys, (
        f"restore-keys debe contener 'saas-db-', es: {restore_keys}"
    )


def test_has_artifact_upload_step(workflow: dict):
    """El job 'run' tiene un step que usa actions/upload-artifact@v4."""
    steps = workflow["jobs"]["run"]["steps"]
    artifact_steps = [
        s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")
    ]
    assert len(artifact_steps) >= 1, "Falta step con actions/upload-artifact"


def test_artifact_retention_days(workflow: dict):
    """El step de upload-artifact tiene retention-days: 30."""
    steps = workflow["jobs"]["run"]["steps"]
    artifact_steps = [
        s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")
    ]
    assert len(artifact_steps) >= 1, "Falta step con actions/upload-artifact"
    retention = artifact_steps[0].get("with", {}).get("retention-days")
    assert retention == 30, f"retention-days debe ser 30, es: {retention}"


def test_has_data_branch_checkout_as_tolerant_fallback(workflow: dict):
    """El checkout de la rama data sigue existiendo como fallback transitorio (F29).

    Debe llevar continue-on-error: true para que el job no falle cuando la
    rama data se borre tras la migración a GitHub Releases.
    """
    steps = workflow["jobs"]["run"]["steps"]
    data_checkouts = [
        s for s in steps
        if s.get("uses", "").startswith("actions/checkout")
        and s.get("with", {}).get("ref") == "data"
        and s.get("with", {}).get("path") == "persist"
    ]
    assert len(data_checkouts) >= 1, (
        "Falta checkout de la rama 'data' con path 'persist' (fallback transitorio F29)"
    )
    assert data_checkouts[0].get("continue-on-error") is True, (
        "El checkout de la rama data debe llevar continue-on-error: true (F29)"
    )


def test_has_required_env_secrets(workflow: dict):
    """El job tiene los secrets requeridos como variables de entorno."""
    job = workflow["jobs"]["run"]
    env = job.get("env", {})
    required_secrets = [
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USER_AGENT",
        "GROQ_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ]
    for secret in required_secrets:
        assert secret in env, f"Falta secret '{secret}' en env del job"

    # Verificar que los providers eliminados NO están en el env
    removed_secrets = ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AI_PROVIDER", "EXTRACTION_PROVIDER"]
    for secret in removed_secrets:
        assert secret not in env, f"Secret eliminado '{secret}' no debería estar en env del job"


def test_has_python_setup(workflow: dict):
    """El job 'run' tiene un step de setup-python con Python 3.11."""
    steps = workflow["jobs"]["run"]["steps"]
    setup_steps = [s for s in steps if s.get("uses", "").startswith("actions/setup-python")]
    assert len(setup_steps) >= 1, "Falta step de actions/setup-python"
    py_version = setup_steps[0].get("with", {}).get("python-version")
    assert py_version == "3.11", f"setup-python debe usar '3.11', usa: {py_version}"


def test_run_pipeline_step_handles_full_scan(workflow: dict):
    """El step Run pipeline contiene '--full-scan' en el script."""
    steps = workflow["jobs"]["run"]["steps"]
    pipeline_steps = [
        s for s in steps
        if "saas_radar.main" in s.get("run", "")
    ]
    assert len(pipeline_steps) >= 1, "Falta step que ejecute 'python -m saas_radar.main'"
    run_script = pipeline_steps[0]["run"]
    assert "--full-scan" in run_script, (
        "El step de run debe incluir lógica para --full-scan"
    )
    assert "full_scan" in run_script, (
        "El step debe referenciar el input full_scan"
    )


def test_permissions_contents_write(workflow: dict):
    """permissions.contents debe ser 'write' (F29: crear/subir/borrar releases)."""
    permissions = workflow.get("permissions")
    assert permissions is not None, "Falta bloque 'permissions'"
    assert permissions.get("contents") == "write", (
        f"permissions.contents debe ser 'write', es: {permissions.get('contents')}"
    )


def test_no_push_to_data_branch(workflow: dict):
    """Regresión-guard F29: la rama data está congelada, ningún step hace git push."""
    steps = workflow["jobs"]["run"]["steps"]
    pushing_steps = [s for s in steps if "git push" in s.get("run", "")]
    assert not pushing_steps, (
        f"La rama data no debe escribirse más (F29). Steps con git push: "
        f"{[s.get('name') for s in pushing_steps]}"
    )


def test_restore_step_downloads_from_db_latest_release(workflow: dict):
    """El restore intenta primero la release db-latest con gh release download (F29)."""
    steps = workflow["jobs"]["run"]["steps"]
    restore_steps = [
        s for s in steps
        if "gh release download db-latest" in s.get("run", "")
    ]
    assert len(restore_steps) >= 1, "Falta restore desde la release db-latest"
    script = restore_steps[0]["run"]
    assert "saas.db.zst" in script, "El restore debe descargar el asset saas.db.zst"
    assert "zstd -d" in script, "El restore debe descomprimir el .zst"
    assert "persist/data/saas.db" in script, (
        "El restore debe conservar el fallback a la rama data (transición F29)"
    )
    assert "GH_TOKEN" in restore_steps[0].get("env", {}), (
        "El step de restore necesita GH_TOKEN para usar gh"
    )


def test_publish_step_uploads_to_releases(workflow: dict):
    """El step de publicación sube el .zst a db-latest y al snapshot diario (F29)."""
    steps = workflow["jobs"]["run"]["steps"]
    publish_steps = [
        s for s in steps
        if "gh release upload db-latest" in s.get("run", "")
        or "gh release create db-latest" in s.get("run", "")
    ]
    assert len(publish_steps) >= 1, "Falta step de publicación a GitHub Releases"
    script = publish_steps[0]["run"]
    assert "VACUUM" in script, "La BD debe compactarse con VACUUM antes de comprimir"
    assert "zstd -T0 -15" in script, "La compresión debe mantener zstd -T0 -15 (F26)"
    assert "--clobber" in script, (
        "La subida a db-latest debe usar --clobber para reemplazar el asset"
    )
    assert "db-$(date -u +%Y%m%d)" in script, "Falta el snapshot diario db-YYYYMMDD"
    assert "runs.tar.gz" in script, (
        "Los JSON de resultados deben empaquetarse en runs.tar.gz para el snapshot"
    )
    assert "GH_TOKEN" in publish_steps[0].get("env", {}), (
        "El step de publicación necesita GH_TOKEN para usar gh"
    )


def test_rotation_step_keeps_seven_and_spares_db_latest(workflow: dict):
    """La rotación borra solo releases db-<8 dígitos>, dejando 7 (F29)."""
    steps = workflow["jobs"]["run"]["steps"]
    rotation_steps = [s for s in steps if "gh release delete" in s.get("run", "")]
    assert len(rotation_steps) == 1, "Debe haber exactamente un step de rotación"
    script = rotation_steps[0]["run"]
    assert "^db-[0-9]{8}$" in script, (
        "El filtro de rotación debe ser la regex anclada ^db-[0-9]{8}$ "
        "(estructuralmente incapaz de matchear db-latest)"
    )
    assert "tail -n +8" in script, "La rotación debe conservar los 7 snapshots más recientes"
    assert "--cleanup-tag" in script, "El borrado debe limpiar también el tag git"
    assert "--yes" in script, "El borrado debe ser no-interactivo (--yes)"


def test_failure_alert_step(workflow: dict):
    """Existe un step con if: failure() que alerta por Telegram vía curl (F29)."""
    steps = workflow["jobs"]["run"]["steps"]
    alert_steps = [
        s for s in steps
        if s.get("if") == "failure()" and "api.telegram.org" in s.get("run", "")
    ]
    assert len(alert_steps) == 1, "Falta step de alerta Telegram con if: failure()"
    script = alert_steps[0]["run"]
    assert 'if [ -n "$TELEGRAM_BOT_TOKEN" ]' in script, (
        "La alerta debe llevar guard para no fallar sin secrets"
    )
    assert "RUN_URL" in script, "La alerta debe incluir el link al run"
    assert "RUN_URL" in alert_steps[0].get("env", {}), (
        "RUN_URL debe componerse en env con github.server_url/repository/run_id"
    )
    assert steps[-1] is alert_steps[0], (
        "La alerta debe ser el último step para cubrir cualquier fallo previo"
    )


def test_workflow_name(workflow: dict):
    """El nombre del workflow es exactamente 'saas-radar pipeline'."""
    assert workflow.get("name") == "saas-radar pipeline", (
        f"Nombre del workflow incorrecto: {workflow.get('name')}"
    )


def test_workflow_job_run_exists(workflow: dict):
    """El workflow tiene un job llamado 'run'."""
    jobs = workflow.get("jobs", {})
    assert "run" in jobs, f"Falta job 'run'. Jobs existentes: {list(jobs.keys())}"


def test_workflow_job_steps_checkout_main(workflow: dict):
    """El job 'run' tiene un step de checkout de la rama main sin 'path'."""
    steps = workflow["jobs"]["run"]["steps"]
    checkout_steps = [s for s in steps if s.get("uses", "").startswith("actions/checkout")]
    assert len(checkout_steps) >= 1, "Falta al menos un step de actions/checkout"
    main_checkout = [s for s in checkout_steps if "path" not in s.get("with", {})]
    assert len(main_checkout) >= 1, "Falta checkout sin 'path' (checkout de main)"


def test_workflow_job_steps_install_deps(workflow: dict):
    """El job 'run' tiene un step que instala dependencias con pip install -e .[dev]."""
    steps = workflow["jobs"]["run"]["steps"]
    install_steps = [
        s for s in steps
        if "pip install" in s.get("run", "") and ".[dev]" in s.get("run", "")
    ]
    assert len(install_steps) >= 1, "Falta step con 'pip install -e .[dev]'"


def test_workflow_job_steps_nltk_download(workflow: dict):
    """El job 'run' tiene un step que descarga las stopwords de NLTK."""
    steps = workflow["jobs"]["run"]["steps"]
    nltk_steps = [
        s for s in steps
        if "nltk" in s.get("run", "") and "stopwords" in s.get("run", "")
    ]
    assert len(nltk_steps) >= 1, "Falta step para descargar NLTK stopwords"


def test_workflow_job_steps_run_pipeline(workflow: dict):
    """El job 'run' tiene un step que ejecuta python -m saas_radar.main."""
    steps = workflow["jobs"]["run"]["steps"]
    pipeline_steps = [
        s for s in steps
        if "saas_radar.main" in s.get("run", "")
    ]
    assert len(pipeline_steps) >= 1, "Falta step que ejecute 'python -m saas_radar.main'"


# ── tuner.yml (F29: mismo restore desde releases + alerta de fallo) ──────────


def test_tuner_workflow_is_valid_yaml(tuner_workflow: dict):
    """El workflow del tuner existe, es YAML válido y tiene el job 'tune'."""
    assert isinstance(tuner_workflow, dict)
    assert "tune" in tuner_workflow.get("jobs", {}), "Falta job 'tune' en tuner.yml"


def test_tuner_restore_downloads_from_db_latest_release(tuner_workflow: dict):
    """El tuner restaura la BD desde db-latest con fallback a la rama data (F29)."""
    steps = tuner_workflow["jobs"]["tune"]["steps"]
    restore_steps = [
        s for s in steps
        if "gh release download db-latest" in s.get("run", "")
    ]
    assert len(restore_steps) >= 1, "Falta restore desde la release db-latest en tuner.yml"
    script = restore_steps[0]["run"]
    assert "persist/data/saas.db" in script, (
        "El destino debe seguir siendo persist/data/saas.db (--db-path del tuner no cambia)"
    )
    assert "zstd -d" in script, "El restore debe descomprimir el .zst"
    assert "GH_TOKEN" in restore_steps[0].get("env", {}), (
        "El step de restore necesita GH_TOKEN para usar gh"
    )


def test_tuner_data_branch_checkout_is_tolerant(tuner_workflow: dict):
    """El checkout de la rama data en tuner.yml lleva continue-on-error: true (F29)."""
    steps = tuner_workflow["jobs"]["tune"]["steps"]
    data_checkouts = [
        s for s in steps
        if s.get("uses", "").startswith("actions/checkout")
        and s.get("with", {}).get("ref") == "data"
    ]
    assert len(data_checkouts) >= 1, "Falta checkout de la rama data (fallback transitorio)"
    assert data_checkouts[0].get("continue-on-error") is True, (
        "El checkout de la rama data debe llevar continue-on-error: true (F29)"
    )


def test_tuner_failure_alert_step(tuner_workflow: dict):
    """tuner.yml tiene la misma alerta Telegram con if: failure() (F29)."""
    steps = tuner_workflow["jobs"]["tune"]["steps"]
    alert_steps = [
        s for s in steps
        if s.get("if") == "failure()" and "api.telegram.org" in s.get("run", "")
    ]
    assert len(alert_steps) == 1, "Falta step de alerta Telegram con if: failure()"
    script = alert_steps[0]["run"]
    assert 'if [ -n "$TELEGRAM_BOT_TOKEN" ]' in script, (
        "La alerta debe llevar guard para no fallar sin secrets"
    )
    assert "saas-radar tuner" in script, "El mensaje debe identificar el workflow del tuner"
    assert steps[-1] is alert_steps[0], (
        "La alerta debe ser el último step para cubrir cualquier fallo previo"
    )
