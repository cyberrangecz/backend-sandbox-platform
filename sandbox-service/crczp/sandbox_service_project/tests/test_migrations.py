"""Tests that the migrations of every app match its models."""

from io import StringIO

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_models_have_no_unmigrated_changes():
    """Every model change, Meta options included, is covered by a migration."""
    output = StringIO()

    try:
        call_command('makemigrations', check=True, dry_run=True, stdout=output)
    except SystemExit:
        pytest.fail(f'Models have changes without a migration:\n{output.getvalue()}')
