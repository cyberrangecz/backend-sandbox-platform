"""Django management command that cleans up the sandboxes trainees allocated themselves."""

from datetime import timedelta
from typing import Any, override

import structlog
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from crczp.sandbox_instance_app.lib import requests as sandbox_requests

LOG = structlog.get_logger()


class Command(BaseCommand):
    """Clean up trainees' own sandboxes once they are older than the configured age."""

    help = (
        'Clean up the sandboxes trainees allocated for themselves once they are older than '
        'trainee_sandbox_cleanup.max_age_hours. Does nothing unless '
        'trainee_sandbox_cleanup.enabled is set. Meant to run periodically, e.g. as a '
        'Kubernetes CronJob; it fails when a unit could not be cleaned up.'
    )
    requires_migrations_checks = True

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only report which sandboxes would be cleaned up.',
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        config = settings.CRCZP_CONFIG.trainee_sandbox_cleanup
        if not config.enabled:
            self.stdout.write('Trainee sandbox cleanup is disabled (trainee_sandbox_cleanup).')
            return

        older_than = timezone.now() - timedelta(hours=config.max_age_hours)
        report = sandbox_requests.cleanup_expired_trainee_units(
            older_than, dry_run=options['dry_run']
        )
        LOG.info(
            'trainee_sandbox_cleanup_finished',
            older_than=older_than.isoformat(),
            dry_run=options['dry_run'],
            cleaned=report.cleaned,
            retried=report.retried,
            skipped=report.skipped,
            failed=report.failed,
        )
        prefix = 'Would clean up' if options['dry_run'] else 'Cleaned up'
        self.stdout.write(
            f'{prefix} {len(report.cleaned)} trainee sandbox(es) created before '
            f'{older_than.isoformat()}, retried {len(report.retried)} failed cleanup(s), '
            f'skipped {len(report.skipped)} not cleanable yet.'
        )
        if report.failed:
            raise CommandError(f'Cleaning up allocation units {report.failed} failed.')
