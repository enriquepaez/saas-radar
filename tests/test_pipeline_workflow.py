"""Tests de validación del workflow GitHub Actions pipeline.yml."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = Path(__file__).parent.parent / ".github" / "workflows" / "pipeline.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    """Carga y parsea el workflow YAML una sola vez por módulo."""
    assert WORKFLOW_PATH.exists(), f"El workflow no existe en {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open() as f:
        return yaml.safe_load(f)


def test_workflow_file_exists_and_is_valid_yaml():
    """El archivo .github/workflows/pipeline.yml existe y es YAML válido."""
    assert WORKFLOW_PATH.exists(), f"Falta {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open() as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "El YAML debe ser un mapeo raíz"


def test_workflow_has_schedule_cron(workflow: dict):
    """El workflow tiene trigger schedule con cron '0 8 * * *' (diario a las 8 UTC)."""
    on_block = workflow.get("on", {})
    assert "schedule" in on_block, "Falta trigger 'schedule'"
    crons = [entry.get("cron") for entry in on_block["schedule"]]
    assert "0 8 * * *" in crons, f"Cron '0 8 * * *' no encontrado. Crons: {crons}"


def test_workflow_has_workflow_dispatch_with_full_scan(workflow: dict):
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
    assert full_scan_input.get("default") == "false", (
        f"Default de full_scan debe ser 'false', es: {full_scan_input.get('default')}"
    )


def test_workflow_has_concurrency_group(workflow: dict):
    """El workflow tiene concurrency group 'saas-radar' con cancel-in-progress: false."""
    concurrency = workflow.get("concurrency")
    assert concurrency is not None, "Falta bloque 'concurrency'"
    assert concurrency.get("group") == "saas-radar", (
        f"concurrency.group debe ser 'saas-radar', es: {concurrency.get('group')}"
    )
    assert concurrency.get("cancel-in-progress") is False, (
        f"cancel-in-progress debe ser false, es: {concurrency.get('cancel-in-progress')}"
    )


def test_workflow_job_run_exists(workflow: dict):
    """El workflow tiene un job llamado 'run'."""
    jobs = workflow.get("jobs", {})
    assert "run" in jobs, f"Falta job 'run'. Jobs existentes: {list(jobs.keys())}"


def test_workflow_job_steps_checkout_main(workflow: dict):
    """El job 'run' tiene un step de checkout de la rama main."""
    steps = workflow["jobs"]["run"]["steps"]
    checkout_steps = [s for s in steps if s.get("uses", "").startswith("actions/checkout")]
    assert len(checkout_steps) >= 1, "Falta al menos un step de actions/checkout"
    # El primer checkout no debe tener 'path' (es el checkout del repo principal/main)
    main_checkout = [s for s in checkout_steps if "path" not in s.get("with", {})]
    assert len(main_checkout) >= 1, "Falta checkout sin 'path' (checkout de main)"


def test_workflow_job_steps_checkout_data_persist(workflow: dict):
    """El job 'run' tiene un step de checkout de la rama data en persist/."""
    steps = workflow["jobs"]["run"]["steps"]
    checkout_steps = [s for s in steps if s.get("uses", "").startswith("actions/checkout")]
    data_checkout = [
        s for s in checkout_steps if s.get("with", {}).get("path") == "persist"
    ]
    assert len(data_checkout) >= 1, (
        "Falta checkout con path='persist' (rama data para persistir la BD)"
    )
    data_step = data_checkout[0]
    assert data_step.get("with", {}).get("ref") == "data", (
        "El checkout de persist/ debe usar ref='data'"
    )


def test_workflow_job_steps_setup_python(workflow: dict):
    """El job 'run' tiene un step de setup-python con Python 3.11."""
    steps = workflow["jobs"]["run"]["steps"]
    setup_steps = [s for s in steps if s.get("uses", "").startswith("actions/setup-python")]
    assert len(setup_steps) >= 1, "Falta step de actions/setup-python"
    py_version = setup_steps[0].get("with", {}).get("python-version")
    assert py_version == "3.11", f"setup-python debe usar '3.11', usa: {py_version}"


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


def test_workflow_job_steps_full_scan_conditional(workflow: dict):
    """El step de run del pipeline maneja el flag --full-scan condicionalmente."""
    steps = workflow["jobs"]["run"]["steps"]
    pipeline_steps = [
        s for s in steps
        if "saas_radar.main" in s.get("run", "")
    ]
    assert len(pipeline_steps) >= 1
    run_script = pipeline_steps[0]["run"]
    assert "--full-scan" in run_script, (
        "El step de run debe incluir lógica para --full-scan"
    )
    assert "full_scan" in run_script, (
        "El step debe referenciar el input full_scan"
    )


def test_workflow_job_steps_copy_outputs(workflow: dict):
    """El job 'run' tiene un step que copia outputs a persist/data/."""
    steps = workflow["jobs"]["run"]["steps"]
    copy_steps = [
        s for s in steps
        if "persist/data" in s.get("run", "") and "saas.db" in s.get("run", "")
    ]
    assert len(copy_steps) >= 1, "Falta step que copie saas.db a persist/data/"


def test_workflow_job_steps_commit_push(workflow: dict):
    """El job 'run' tiene un step que hace commit y push a la rama data."""
    steps = workflow["jobs"]["run"]["steps"]
    commit_steps = [
        s for s in steps
        if "git commit" in s.get("run", "") and "git push" in s.get("run", "")
    ]
    assert len(commit_steps) >= 1, "Falta step con 'git commit' y 'git push'"


def test_workflow_job_steps_commit_guard(workflow: dict):
    """El step de commit usa git diff --cached --quiet para no commitear si no hay cambios."""
    steps = workflow["jobs"]["run"]["steps"]
    # Buscamos el step que tiene git commit + git push Y también la guarda diff --cached --quiet
    # (no el step de inicialización de la rama data, que también tiene git commit/push)
    commit_steps = [
        s for s in steps
        if "git commit" in s.get("run", "")
        and "git push" in s.get("run", "")
        and "git diff --cached --quiet" in s.get("run", "")
    ]
    assert len(commit_steps) >= 1, (
        "Falta step con 'git commit', 'git push' y guarda 'git diff --cached --quiet'"
    )
    run_script = commit_steps[0]["run"]
    assert "git diff --cached --quiet" in run_script, (
        "El step de commit debe usar 'git diff --cached --quiet' como guarda"
    )


def test_workflow_job_env_secrets(workflow: dict):
    """El job tiene variables de entorno apuntando a los secrets necesarios."""
    job = workflow["jobs"]["run"]
    env = job.get("env", {})
    required_secrets = [
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "AI_PROVIDER",
    ]
    for secret in required_secrets:
        assert secret in env, f"Falta secret '{secret}' en env del job"


def test_workflow_name(workflow: dict):
    """El nombre del workflow es exactamente 'saas-radar pipeline'."""
    assert workflow.get("name") == "saas-radar pipeline", (
        f"Nombre del workflow incorrecto: {workflow.get('name')}"
    )
