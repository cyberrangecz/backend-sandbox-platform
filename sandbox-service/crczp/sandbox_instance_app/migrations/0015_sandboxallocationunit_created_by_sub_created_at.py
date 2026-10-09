import django.utils.timezone
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import OuterRef, Subquery


def backfill_created_at(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Date existing units by their allocation request instead of by this migration."""
    allocation_unit = apps.get_model('sandbox_instance_app', 'SandboxAllocationUnit')
    allocation_request = apps.get_model('sandbox_instance_app', 'AllocationRequest')
    request_created = allocation_request.objects.filter(allocation_unit=OuterRef('pk')).values(
        'created'
    )[:1]
    allocation_unit.objects.filter(allocation_request__isnull=False).update(
        created_at=Subquery(request_created)
    )


class Migration(migrations.Migration):
    dependencies = [
        ('sandbox_instance_app', '0014_sandboxnetbirdresources'),
    ]

    operations = [
        migrations.AddField(
            model_name='sandboxallocationunit',
            name='created_at',
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                help_text='When this allocation unit was created.',
            ),
        ),
        migrations.AddField(
            model_name='sandboxallocationunit',
            name='created_by_sub',
            field=models.CharField(
                blank=True,
                default=None,
                help_text='OIDC sub of the trainee who allocated this unit for themselves; '
                'null for units built by an organizer.',
                max_length=255,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name='sandboxallocationunit',
            index=models.Index(fields=['created_by_sub'], name='sau_created_by_sub_idx'),
        ),
        migrations.RunPython(backfill_created_at, migrations.RunPython.noop),
    ]
