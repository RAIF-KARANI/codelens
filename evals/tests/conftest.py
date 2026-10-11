import pytest

from evals.graph import CodeGraph
from evals.tests.toy import write_toy_repo


@pytest.fixture(scope="session")
def toy_graph(tmp_path_factory: pytest.TempPathFactory) -> CodeGraph:
    return CodeGraph.from_path(write_toy_repo(tmp_path_factory.mktemp("toy")))
