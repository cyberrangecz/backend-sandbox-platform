from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import OuterRef, Subquery


def link_netbird_rows_to_units(apps: Apps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Link every NetBird row to the allocation unit of its sandbox."""
    sandbox_model = apps.get_model('sandbox_instance_app', 'Sandbox')
    unit_of_sandbox = Subquery(
        sandbox_model.objects.filter(pk=OuterRef('sandbox_id')).values('allocation_unit_id')[:1]
    )
    for model_name in (
        'SandboxNetbirdAccess',
        'SandboxNetbirdResources',
        'SandboxNetbirdEphemeralKey',
    ):
        apps.get_model('sandbox_instance_app', model_name).objects.filter(
            allocation_unit__isnull=True, sandbox__isnull=False
        ).update(allocation_unit_id=unit_of_sandbox)


class Migration(migrations.Migration):
    dependencies = [
        ('sandbox_instance_app', '0025_netbird_allocation_unit'),
    ]

    operations = [
        migrations.RunPython(link_netbird_rows_to_units, migrations.RunPython.noop),
    ]
