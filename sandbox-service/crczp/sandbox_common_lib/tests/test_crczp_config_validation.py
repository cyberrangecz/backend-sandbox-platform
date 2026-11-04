"""Tests for CrczpConfiguration attribute validators."""

import pytest

from crczp.sandbox_common_lib import crczp_config_validation


class TestValidateNetbirdKeyExpiry:
    """Tests for the Netbird setup-key expiry bounds check."""

    @pytest.mark.parametrize(
        'key_expiry_seconds',
        [
            crczp_config_validation.NETBIRD_KEY_EXPIRY_MIN_SECONDS,
            crczp_config_validation.NETBIRD_KEY_EXPIRY_MAX_SECONDS,
            1209600,  # the NetbirdConfiguration default (14 days)
        ],
    )
    def test_accepts_values_within_range(self, key_expiry_seconds):
        assert crczp_config_validation.validate_netbird_key_expiry(object(), key_expiry_seconds)

    @pytest.mark.parametrize(
        'key_expiry_seconds',
        [
            crczp_config_validation.NETBIRD_KEY_EXPIRY_MIN_SECONDS - 1,
            crczp_config_validation.NETBIRD_KEY_EXPIRY_MAX_SECONDS + 1,
            0,
            -1,
        ],
    )
    def test_rejects_values_outside_range(self, key_expiry_seconds):
        with pytest.raises(ValueError, match='key_expiry_seconds'):
            crczp_config_validation.validate_netbird_key_expiry(object(), key_expiry_seconds)


class TestValidateTraineeCleanupMaxAgeHours:
    """Tests for the trainee sandbox cleanup age check."""

    @pytest.mark.parametrize('max_age_hours', [1, 24, 24 * 30])
    def test_accepts_an_hour_or_more(self, max_age_hours):
        """An hour or more is a valid age."""
        assert crczp_config_validation.validate_trainee_cleanup_max_age_hours(
            object(), max_age_hours
        )

    @pytest.mark.parametrize('max_age_hours', [0, -1])
    def test_rejects_less_than_an_hour(self, max_age_hours):
        """Less than an hour would remove sandboxes trainees just got."""
        with pytest.raises(ValueError, match='max_age_hours'):
            crczp_config_validation.validate_trainee_cleanup_max_age_hours(object(), max_age_hours)


class TestValidateLostJobObserverGraceSeconds:
    """Tests for the lost job observer grace check."""

    @pytest.mark.parametrize('grace_seconds', [1, 900, 3600])
    def test_accepts_a_second_or_more(self, grace_seconds):
        """A second or more is a valid grace."""
        assert crczp_config_validation.validate_lost_job_observer_grace_seconds(
            object(), grace_seconds
        )

    @pytest.mark.parametrize('grace_seconds', [0, -1])
    def test_rejects_less_than_a_second(self, grace_seconds):
        """No grace would fail jobs RQ is still collecting."""
        with pytest.raises(ValueError, match='grace_seconds'):
            crczp_config_validation.validate_lost_job_observer_grace_seconds(
                object(), grace_seconds
            )


class TestValidateNetbirdTeardownBudgetSeconds:
    """Tests for the Netbird teardown budget check."""

    @pytest.mark.parametrize('budget_seconds', [1, 60, 600])
    def test_accepts_a_second_or_more(self, budget_seconds):
        """A positive budget is a valid one."""
        assert crczp_config_validation.validate_netbird_teardown_budget_seconds(
            object(), budget_seconds
        )

    @pytest.mark.parametrize('budget_seconds', [0, -1])
    def test_rejects_a_budget_that_is_spent_before_it_starts(self, budget_seconds):
        """A zero budget would make every NetBird teardown fail at once."""
        with pytest.raises(ValueError, match='teardown_budget_seconds'):
            crczp_config_validation.validate_netbird_teardown_budget_seconds(
                object(), budget_seconds
            )
