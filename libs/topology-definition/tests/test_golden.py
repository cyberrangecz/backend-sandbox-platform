"""
Golden tests: real topology definitions load to exactly what they loaded to before.

tests/assets/golden holds topology.yml files of the cyberrangecz organisation, each naming
its source on its first line, and the repository's own fixtures in tests/assets join them.
tests/assets/golden/expected/<name>.json records what each one loads to: the definition,
field by field, or for an invalid one the error and the line it points at.

After an intended change, regenerate the records from this directory and review their diff:

    UPDATE_GOLDEN=1 uv run --group test pytest tests/test_golden.py
"""

import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from crczp.topology_definition.models import TopologyDefinition

ASSETS_DIR = Path(__file__).parent / 'assets'
GOLDEN_DIR = ASSETS_DIR / 'golden'
EXPECTED_DIR = GOLDEN_DIR / 'expected'
CORPUS = sorted([*GOLDEN_DIR.glob('*.yml'), *ASSETS_DIR.glob('*.yml')])
UPDATE = os.environ.get('UPDATE_GOLDEN') == '1'


def canonical(value: Any) -> Any:
    """
    A JSON-ready view of a loaded value: models as field name to value, enums by lowercase name.
    """
    if value is None or isinstance(value, str | int | float):
        return value
    if isinstance(value, Enum):
        return value.name.lower()
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, BaseModel):
        return {name: canonical(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    raise TypeError(f'No canonical form for {type(value).__name__}: {value!r}')


def load_result(path: Path) -> dict[str, Any]:
    """
    What the definition at path loads to: the definition, or the error and its line.
    """
    text = path.read_text(encoding='utf-8')
    try:
        return {'definition': canonical(TopologyDefinition.load(text))}
    except ValueError as exc:  # an invalid definition: its error is recorded
        message = str(exc)
        line = re.search(r'start:.*?line (\d+)', message)
        return {
            'error': message.split('\n', 1)[0],
            'line': int(line.group(1)) if line else None,
        }


@pytest.mark.parametrize('path', CORPUS, ids=lambda path: path.stem)
def test_loads_as_recorded(path: Path) -> None:
    """
    The definition loads to its recorded result.
    """
    expected_file = EXPECTED_DIR / f'{path.stem}.json'
    result = load_result(path)
    if UPDATE:
        EXPECTED_DIR.mkdir(exist_ok=True)
        expected_file.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

    assert result == json.loads(expected_file.read_text(encoding='utf-8'))
