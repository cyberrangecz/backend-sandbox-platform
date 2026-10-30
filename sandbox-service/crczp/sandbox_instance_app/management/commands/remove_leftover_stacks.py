"""Django management command that removes the stacks no allocation unit owns."""

from typing import Any, override

from django.core.management.base import BaseCommand, CommandError, CommandParser

from crczp.sandbox_instance_app.lib import stack_leftovers


class Command(BaseCommand):
    """Remove the leftovers of stacks whose allocation unit no longer exists."""

    help = (
        'Remove every stack that no allocation unit owns, the way the cleanup does: its cloud '
        'resources, terraform workspace, NetBird objects and jump host home. Meant to run once '
        'by hand; it fails when a listing or a removal failed.'
    )
    requires_migrations_checks = True

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only list the stacks that would be removed, with the places that hold them.',
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        dry_run = options['dry_run']
        report = stack_leftovers.remove_leftover_stacks(dry_run=dry_run)
        verb = 'Would remove' if dry_run else 'Removed'
        for stack_name, found_by in report.removed.items():
            self.stdout.write(f'{verb} {stack_name} (found in: {", ".join(found_by)})')
        self.stdout.write(
            f'{verb} {len(report.removed)} stack(s), left {len(report.skipped)} owned stack(s).'
        )
        if report.failed:
            details = '; '.join(f'{name}: {error}' for name, error in report.failed.items())
            raise CommandError(f'{len(report.failed)} step(s) failed: {details}')
