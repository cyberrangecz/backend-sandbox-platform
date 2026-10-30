from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def assign_in_flight_requests_to_groups(
    apps: Apps, _schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """
    Count every allocation request that is still building in the request group it was queued
    with, so that the group sums up the build once it ends.

    An open group has room for the units that have not ended yet. They are the unassigned
    allocation requests of its pool with an unfinished stage, oldest first.
    """
    group_model = apps.get_model('sandbox_instance_app', 'SandboxRequestGroup')
    request_model = apps.get_model('sandbox_instance_app', 'AllocationRequest')
    for group in group_model.objects.order_by('id'):
        open_places = group.unit_count - group.finished_count
        if open_places <= 0:
            continue
        in_flight_request_ids = list(
            request_model.objects
            .filter(
                allocation_unit__pool_id=group.pool_id,
                request_group__isnull=True,
                stages__finished=False,
            )
            .distinct()
            .order_by('id')
            .values_list('id', flat=True)[:open_places]
        )
        request_model.objects.filter(id__in=in_flight_request_ids).update(request_group=group)


class Migration(migrations.Migration):
    dependencies = [
        ('sandbox_instance_app', '0026_netbird_allocation_unit_backfill'),
    ]

    operations = [
        migrations.RunPython(assign_in_flight_requests_to_groups, migrations.RunPython.noop),
    ]
