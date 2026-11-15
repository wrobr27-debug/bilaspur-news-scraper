import pytest

from bilaspur_scraper.errors import LifecycleError
from bilaspur_scraper.registry.lifecycle import assert_transition
from bilaspur_scraper.schemas.enums import LifecycleState as S


def test_main_path_is_allowed():
    assert_transition(S.CANDIDATE, S.DISCOVERED)
    assert_transition(S.DISCOVERED, S.VERIFICATION_PENDING)
    assert_transition(S.VERIFICATION_PENDING, S.VERIFIED)
    assert_transition(S.VERIFIED, S.ENABLED)


def test_candidate_cannot_skip_to_enabled():
    with pytest.raises(LifecycleError):
        assert_transition(S.CANDIDATE, S.ENABLED)


def test_discovered_cannot_go_straight_to_enabled():
    with pytest.raises(LifecycleError):
        assert_transition(S.DISCOVERED, S.ENABLED)


def test_verification_pending_cannot_go_straight_to_enabled():
    with pytest.raises(LifecycleError):
        assert_transition(S.VERIFICATION_PENDING, S.ENABLED)


def test_verification_pending_outcomes():
    assert_transition(S.VERIFICATION_PENDING, S.REJECTED)
    assert_transition(S.VERIFICATION_PENDING, S.DISABLED)


def test_rejected_reopening_requires_reason():
    with pytest.raises(LifecycleError):
        assert_transition(S.REJECTED, S.DISCOVERED)
    assert_transition(S.REJECTED, S.DISCOVERED,
                      reason="site ownership re-confirmed with new evidence")


def test_rejected_is_not_terminal_but_cannot_enable_directly():
    with pytest.raises(LifecycleError):
        assert_transition(S.REJECTED, S.ENABLED)


def test_rejected_can_reenter_verification_path():
    assert_transition(S.REJECTED, S.DISCOVERED, reason="new evidence")
    assert_transition(S.DISCOVERED, S.VERIFICATION_PENDING)


def test_disabled_reactivation_requires_reason():
    with pytest.raises(LifecycleError):
        assert_transition(S.DISABLED, S.ENABLED)
    assert_transition(S.DISABLED, S.ENABLED, reason="transient outage resolved")


def test_excluded_is_terminal():
    with pytest.raises(LifecycleError):
        assert_transition(S.EXCLUDED, S.ENABLED)
    with pytest.raises(LifecycleError):
        assert_transition(S.EXCLUDED, S.CANDIDATE)


def test_enabled_can_be_disabled_or_excluded():
    assert_transition(S.ENABLED, S.DISABLED)
    assert_transition(S.ENABLED, S.EXCLUDED)


def test_verified_can_be_reverified_or_excluded():
    assert_transition(S.VERIFIED, S.VERIFICATION_PENDING)
    assert_transition(S.VERIFIED, S.EXCLUDED)
