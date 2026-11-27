from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Count


def set_pool_size_to_allocation_unit_count(
    apps: Apps, _schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Set the stored size of every pool to the number of allocation units it holds."""
    pool_model = apps.get_model('sandbox_instance_app', 'Pool')
    for pool in pool_model.objects.annotate(unit_count=Count('allocation_units')):
        if pool.size != pool.unit_count:
            pool_model.objects.filter(pk=pool.pk).update(size=pool.unit_count)


class Migration(migrations.Migration):
    dependencies = [
        ('sandbox_instance_app', '0027_request_group_backfill'),
    ]

    operations = [
        migrations.RunPython(set_pool_size_to_allocation_unit_count, migrations.RunPython.noop),
    ]
