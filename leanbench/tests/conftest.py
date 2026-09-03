from __future__ import annotations

import sys

import pytest
from lb_paths import SRC

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def config():
    from leanbench.config import resolve_config

    # `use_env=False` and no repo layer: a developer's own ~/.config must never be able
    # to change what the test suite asserts.
    return resolve_config(use_env=False, search_from=None)
