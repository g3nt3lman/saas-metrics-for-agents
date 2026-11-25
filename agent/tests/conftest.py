import pytest

from semantic import PROJECT_DIR, SemanticLayer


@pytest.fixture(scope="session")
def layer() -> SemanticLayer:
    if not (PROJECT_DIR / "target" / "semantic_manifest.json").exists():
        pytest.skip("run `make build` first: the MCP tests query the built warehouse")
    return SemanticLayer()
