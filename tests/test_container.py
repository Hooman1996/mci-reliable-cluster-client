from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_runtime_stage_is_thin_non_root_and_safe_by_default() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM python:3.12-slim-bookworm AS runtime\n", maxsplit=1)[1]

    assert dockerfile.count("FROM python:3.12-slim-bookworm") == 2
    assert "--no-index --find-links=/wheels" in runtime
    assert "--mount=type=bind,from=builder,source=/wheels,target=/wheels" in runtime
    assert "COPY " not in runtime
    assert "apt-get" not in dockerfile
    assert "PYTHONUNBUFFERED=1" in runtime
    assert "PYTHONDONTWRITEBYTECODE=1" in runtime
    assert "chmod 0555 /app" in runtime
    assert "--no-compile" in runtime
    assert "USER 10001:10001" in runtime
    assert 'ENTRYPOINT ["mci-cluster"]' in runtime
    assert 'CMD ["--help"]' in runtime
    assert "HEALTHCHECK" not in dockerfile
    assert all(package not in runtime for package in ("pytest", "coverage", "mypy", "ruff"))


def test_docker_context_excludes_non_runtime_material() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {
        ".git",
        ".github",
        ".agents",
        ".codex",
        "AGENTS.md",
        "reference",
        "tests",
        "manifests",
        "docs",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".env",
    } <= ignored
    assert {"Dockerfile", "pyproject.toml", "README.md", "src"}.isdisjoint(ignored)
