"""checkers/src/arch_check/rules/delivery.py: layout, images, tooling, the client stack, exceptions, logs, ADRs, versions."""

import shutil

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import ADR, check, check_json, rules_found, write_project

WORKSPACE = '[tool.arch-check]\npackage = "acme"\n\n[tool.uv.workspace]\nmembers = ["om", "infra", "services/*", "workers/*"]\n'
MEMBERS = ("om", "infra", "services/api", "workers/maintenance")
SVC = "services/api/src/acme/services/api"
WRK = "workers/maintenance/src/acme/workers/maintenance"


def workspace(**extra):
    """The base tree made a uv workspace in the guideline's shape: every member a distribution with tests."""
    files = {f"{m}/pyproject.toml": f'[project]\nname = "acme-{m.rpartition("/")[2]}"\n' for m in MEMBERS}
    files.update({f"{m}/tests/test_it.py": "" for m in MEMBERS})
    files["services/api/pyproject.toml"] += '\n[project.scripts]\nacme-api = "acme.services.api.main:main"\n'
    files["workers/maintenance/pyproject.toml"] += '\n[project.scripts]\nacme-maintenance = "x:main"\n'
    files.update({f"{SVC}/main.py": "", f"{SVC}/routers/__init__.py": "", f"{SVC}/types/__init__.py": ""})
    files[f"{WRK}/main.py"] = ""
    files.update(extra)
    return files


def found(tmp_path, rule, files, pyproject=WORKSPACE):
    write_project(tmp_path, files, pyproject=pyproject)
    code, report = check_json(tmp_path, "--rule", rule)
    return code, rules_found(report)


# --- DEL-07


def test_del_07_one_om_distribution_passes(tmp_path):
    assert found(tmp_path, "DEL-07", workspace()) == (0, [])


def test_del_07_a_second_om_distribution_or_code_under_deployment_fails(tmp_path):
    files = workspace(**{"om/tasks/pyproject.toml": "", "deployment/tool/pyproject.toml": ""})
    code, where = found(tmp_path, "DEL-07", files)
    assert code == 1
    assert sorted(p for _, p, _ in where) == ["deployment/tool/pyproject.toml", "om/tasks/pyproject.toml"]


def test_del_07_a_worker_under_services_fails(tmp_path):
    files = workspace(**{"services/sweeper/src/acme/workers/sweeper/__init__.py": ""})
    code, where = found(tmp_path, "DEL-07", files)
    assert (code, [p for _, p, _ in where]) == (1, ["services/sweeper/src/acme/workers/sweeper/__init__.py"])


# --- DEL-08


def test_del_08_the_src_layout_with_tests_passes(tmp_path):
    assert found(tmp_path, "DEL-08", workspace()) == (0, [])


def test_del_08_a_member_without_tests_fails(tmp_path):
    files = workspace()
    write_project(tmp_path, files, pyproject=WORKSPACE)
    shutil.rmtree(tmp_path / "infra/tests")
    code, report = check_json(tmp_path, "--rule", "DEL-08")
    assert (code, rules_found(report)) == (1, [("DEL-08", "infra/pyproject.toml", 1)])


def test_del_08_a_second_root_package_a_test_in_src_and_a_path_edit_fail(tmp_path):
    files = workspace(
        **{
            "infra/src/other/__init__.py": "",
            "om/src/acme/om/test_rules.py": "",
            "om/tests/conftest.py": "import sys\nsys.path.insert(0, 'src')\n",
        }
    )
    code, where = found(tmp_path, "DEL-08", files)
    assert code == 1
    assert sorted(p for _, p, _ in where) == ["infra/pyproject.toml", "om/src/acme/om/test_rules.py", "om/tests/conftest.py"]


def test_del_08_a_stdlib_root_package_fails(tmp_path):
    write_project(tmp_path, {"om/src/json/om/__init__.py": ""})
    code, out, _ = check(tmp_path, "--package", "json", "--rule", "DEL-08")
    assert code == 1
    assert "shadows a standard-library module" in out


# --- DEL-09


def test_del_09_services_and_workers_in_one_shape_pass(tmp_path):
    assert found(tmp_path, "DEL-09", workspace()) == (0, [])


def test_del_09_a_worker_with_routers_and_a_service_with_no_script_fail(tmp_path):
    files = workspace(**{f"{WRK}/routers/__init__.py": "", "services/api/pyproject.toml": '[project]\nname = "a"\n'})
    code, where = found(tmp_path, "DEL-09", files)
    assert code == 1
    assert sorted(p for _, p, _ in where) == ["services/api/pyproject.toml", f"{WRK}/routers"]


# --- DEL-10

GOOD_IMAGE = """FROM python:3.14-slim AS build
RUN uv sync --frozen --no-dev \\
    --package acme-api
FROM python:3.14-slim
USER acme
HEALTHCHECK CMD curl -f http://localhost:8000/healthz
ENTRYPOINT ["/entrypoint.sh"]
"""


def test_del_10_a_central_two_stage_non_root_image_passes(tmp_path):
    portal = (
        "FROM node:24 AS build\nRUN pnpm install --frozen-lockfile\n"
        "FROM nginxinc/nginx-unprivileged:1.30\nHEALTHCHECK CMD wget -q -O- http://localhost:8080/\n"
    )
    files = {"deployment/docker/api.Dockerfile": GOOD_IMAGE, "deployment/docker/portal.Dockerfile": portal}
    assert found(tmp_path, "DEL-10", files) == (0, [])


def test_del_10_a_dockerfile_in_a_service_folder_fails(tmp_path):
    code, where = found(tmp_path, "DEL-10", {"services/api/Dockerfile": GOOD_IMAGE})
    assert (code, where) == (1, [("DEL-10", "services/api/Dockerfile", 1)])


def test_del_10_one_stage_root_no_healthcheck_and_an_unlocked_install_fail(tmp_path):
    image = "FROM python:3.14-slim\nRUN uv sync --no-dev\n"
    code, where = found(tmp_path, "DEL-10", {"deployment/docker/api.Dockerfile": image})
    assert code == 1
    assert sorted(line for _, _, line in where) == [1, 1, 1, 2]


def test_del_10_an_unprivileged_base_is_an_option(tmp_path):
    pyproject = WORKSPACE + '\n[tool.arch-check.options.DEL-10]\nunprivileged_bases = ["acme/base"]\n'
    image = "FROM python:3.14 AS b\nFROM acme/base:1.2\nHEALTHCHECK CMD true\n"
    assert found(tmp_path, "DEL-10", {"deployment/docker/x.Dockerfile": image}, pyproject) == (0, [])


# --- DEL-11


def test_del_11_tooling_at_the_root_passes(tmp_path):
    files = workspace(**{"ruff.toml": "", "Makefile": "check: lint test\n\ttrue\n"})
    assert found(tmp_path, "DEL-11", files) == (0, [])


def test_del_11_member_lint_config_and_no_check_target_fail(tmp_path):
    files = workspace(
        **{
            "om/pyproject.toml": '[project]\nname = "acme-om"\n\n[tool.ruff]\nline-length = 100\n',
            "infra/ruff.toml": "",
            "Makefile": "test:\n\ttrue\n",
            "apps/portal/package.json": "{}",
        }
    )
    code, where = found(tmp_path, "DEL-11", files)
    assert code == 1
    assert sorted(p for _, p, _ in where) == [
        "Makefile",
        "infra/ruff.toml",
        "om/pyproject.toml",
        "package.json",
        "pnpm-workspace.yaml",
    ]


# --- DEL-12 and DEL-13

PORTAL = '{"dependencies": {"react": "^19", "@tanstack/react-query": "^5", "zustand": "^5"}, "devDependencies": {"vite": "^8"}}'


def test_del_12_and_13_react_on_vite_with_query_and_zustand_pass(tmp_path):
    files = {"apps/portal/package.json": PORTAL, "apps/cli/pyproject.toml": ""}
    assert found(tmp_path, "DEL-12", files) == (0, [])
    assert found(tmp_path, "DEL-13", files) == (0, [])


def test_del_12_a_second_framework_and_a_cli_in_typescript_fail(tmp_path):
    files = {"apps/portal/package.json": '{"dependencies": {"react": "1", "next": "15"}}', "apps/cli/package.json": "{}"}
    code, where = found(tmp_path, "DEL-12", files)
    assert code == 1
    assert sorted(p for _, p, _ in where) == ["apps/cli", "apps/portal/package.json", "apps/portal/package.json"]


def test_del_13_a_third_state_library_fails(tmp_path):
    files = {"apps/portal/package.json": '{"dependencies": {"react": "1", "@reduxjs/toolkit": "2"}}'}
    assert found(tmp_path, "DEL-13", files) == (1, [("DEL-13", "apps/portal/package.json", 1)])


# --- DEL-18

OM_EXC = "om/src/acme/om/exceptions.py"
PLATFORM = (
    "class PlatformException(Exception):\n    http_status = 500\n    code = 'x'\n\nclass NotFound(PlatformException):\n    pass\n"
)


def test_del_18_exceptions_rooted_at_the_platform_pass(tmp_path):
    files = {
        OM_EXC: PLATFORM,
        "om/src/acme/om/tasks/impl.py": (
            "from ..exceptions import NotFound\n\nclass TaskMissing(NotFound):\n    pass\n\nclass _Retry(Exception):\n    pass\n"
        ),
    }
    assert found(tmp_path, "DEL-18", files) == (0, [])


def test_del_18_a_bare_exception_and_a_web_import_in_the_om_fail(tmp_path):
    files = {
        OM_EXC: PLATFORM,
        "om/src/acme/om/tasks/impl.py": "from fastapi import HTTPException\n\nclass Oops(ValueError):\n    pass\n",
    }
    code, where = found(tmp_path, "DEL-18", files)
    assert code == 1
    assert [line for _, _, line in where] == [1, 3]


# --- DEL-19


def test_del_19_module_loggers_and_boot_configuration_pass(tmp_path):
    files = {
        "om/src/acme/om/tasks/impl.py": "import logging\n\nlog = logging.getLogger(__name__)\n",
        "infra/src/acme/infra/observability.py": "import logging\n\nroot = logging.getLogger()\nroot.setLevel('INFO')\n",
    }
    assert found(tmp_path, "DEL-19", files) == (0, [])


def test_del_19_a_named_logger_a_level_set_and_a_second_library_fail(tmp_path):
    source = "import logging\nimport structlog\n\nlog = logging.getLogger('tasks')\nlog.setLevel(10)\n"
    code, where = found(tmp_path, "DEL-19", {"om/src/acme/om/tasks/impl.py": source})
    assert code == 1
    assert [line for _, _, line in where] == [2, 4, 5]


# --- DEL-20


def test_del_20_direct_telemetry_passes(tmp_path):
    files = {"infra/src/acme/infra/observability.py": "from prometheus_client import Counter\nfrom opentelemetry import trace\n"}
    assert found(tmp_path, "DEL-20", files) == (0, [])


def test_del_20_a_wrapper_or_a_second_metrics_system_fails(tmp_path):
    files = {"infra/src/acme/infra/observability.py": "import statsd\n\nclass MetricsInterface:\n    pass\n"}
    code, where = found(tmp_path, "DEL-20", files)
    assert (code, [line for _, _, line in where]) == (1, [1, 3])


# --- DEL-23

GOOD_ADR = "# 1. One root package\n\nDate: 2026-09-16\n\n## Context\n\nx\n\n## Decision\n\ny\n\n## Consequences\n\nz\n"


def test_del_23_dated_adrs_and_resolving_citations_pass(tmp_path):
    files = {ADR: GOOD_ADR, "om/src/acme/om/tasks/impl.py": "# The root package is ADR 0001.\n"}
    assert found(tmp_path, "DEL-23", files) == (0, [])


def test_del_23_a_missing_section_and_a_dangling_citation_fail(tmp_path):
    files = {ADR: "# 1. One root package\n\n## Context\n\nx\n", "Makefile": "x:\n\ttrue  # see ADR-0042\n"}
    code, where = found(tmp_path, "DEL-23", files)
    assert code == 1
    assert sorted((p, line) for _, p, line in where) == [("Makefile", 2), (ADR, 1), (ADR, 1)]


# --- DEL-26


def test_del_26_stable_versions_that_agree_pass(tmp_path):
    files = {
        ".python-version": "3.14\n",
        ".nvmrc": "24.21.0\n",
        "deployment/docker/api.Dockerfile": "FROM python:3.14.2-slim AS b\nFROM python:3.14-slim\n",
        "deployment/docker/portal.Dockerfile": (
            "FROM node:24.21.0-alpine AS b\nFROM nginxinc/nginx-unprivileged:1.30-alpine-slim\n"
        ),
        ".github/workflows/ci.yml": "      - uses: actions/setup-python@v6\n        with:\n          python-version: '3.14'\n",
    }
    assert found(tmp_path, "DEL-26", files) == (0, [])


def test_del_26_a_pre_release_and_a_disagreement_fail(tmp_path):
    files = {
        ".python-version": "3.14\n",
        "deployment/docker/api.Dockerfile": "FROM python:3.15.0rc1-slim AS b\nFROM python:3.13-slim\n",
        ".github/workflows/ci.yml": "          node-version: 25.0.0-beta.1\n",
    }
    code, where = found(tmp_path, "DEL-26", files)
    assert code == 1
    assert sorted((p, line) for _, p, line in where) == [
        (".github/workflows/ci.yml", 1),
        ("deployment/docker/api.Dockerfile", 1),
        ("deployment/docker/api.Dockerfile", 1),
        ("deployment/docker/api.Dockerfile", 2),
    ]


# --- DEL-29

INFRA_EXC = "infra/src/acme/infra/exceptions.py"
INFRA = (
    "class InfraException(Exception):\n    http_status: int = 500\n    code: str = 'infra_error'\n\n"
    "class CacheDown(InfraException):\n    pass\n"
)


def test_del_29_the_infra_root_translated_by_fields_passes(tmp_path):
    files = {INFRA_EXC: INFRA, f"{SVC}/gateway/errors.py": "def h(e):\n    return e.http_status\n"}
    assert found(tmp_path, "DEL-29", files) == (0, [])


def test_del_29_a_root_without_a_code_and_a_catch_by_name_fail(tmp_path):
    files = {
        INFRA_EXC: "class InfraException(Exception):\n    http_status = 500\n\nclass CacheDown(InfraException):\n    pass\n",
        "om/src/acme/om/tasks/impl.py": (
            "from acme.infra.exceptions import CacheDown\n\ntry:\n    pass\nexcept CacheDown:\n    pass\n"
        ),
    }
    code, where = found(tmp_path, "DEL-29", files)
    assert code == 1
    assert sorted(p for _, p, _ in where) == [INFRA_EXC, "om/src/acme/om/tasks/impl.py"]


# --- DEL-35


def test_del_35_bounded_labels_pass(tmp_path):
    source = "from prometheus_client import Counter\n\nC = Counter('n', 'd', ['route', 'status'])\n"
    assert found(tmp_path, "DEL-35", {"infra/src/acme/infra/observability.py": source}) == (0, [])


def test_del_35_an_id_label_fails(tmp_path):
    source = "import prometheus_client\n\nC = prometheus_client.Counter('n', 'd', labelnames=('org_id', 'outcome'))\n"
    code, where = found(tmp_path, "DEL-35", {"infra/src/acme/infra/observability.py": source})
    assert (code, where) == (1, [("DEL-35", "infra/src/acme/infra/observability.py", 3)])
