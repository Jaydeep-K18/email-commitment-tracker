"""The contract files Node is tested against must match what Python produces.

If this fails, a Python-side list or the tier policy changed and the shared
TypeScript package has not been told. Run ``python -m scripts.export_contracts``
and then the TypeScript tests, which will point at whatever needs updating.
"""
from __future__ import annotations

import pytest

from scripts.export_contracts import CONTRACTS_DIR, render


@pytest.mark.parametrize("name", sorted(render()))
def test_the_committed_contract_is_current(name):
    committed = (CONTRACTS_DIR / name).read_text(encoding="utf-8")
    assert committed == render()[name], (
        f"{name} is out of date — run: python -m scripts.export_contracts"
    )
