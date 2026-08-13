from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import pytest
from ai_platform_api.modules.identity.domain.enterprise import (
    InvalidInvitationTransitionError,
    InvalidMembershipTransitionError,
    MembershipType,
    WorkspaceInvitation,
    WorkspaceMembership,
)

NOW = datetime(2026, 8, 14, tzinfo=UTC)
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000071")
OWNER_ID = UUID("10000000-0000-4000-8000-000000000071")
MEMBER_ID = UUID("10000000-0000-4000-8000-000000000072")


def membership(
    *,
    membership_type: MembershipType = "member",
    status: Literal["active", "disabled", "left"] = "active",
) -> WorkspaceMembership:
    return WorkspaceMembership(
        membership_id=UUID("30000000-0000-4000-8000-000000000071"),
        workspace_id=WORKSPACE_ID,
        account_id=MEMBER_ID,
        membership_type=membership_type,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        version=1,
    )


def test_member_lifecycle_supports_leave_disable_and_reactivate() -> None:
    assert membership().leave(occurred_at=NOW + timedelta(minutes=1)).status == "left"
    disabled = membership().disable(occurred_at=NOW + timedelta(minutes=1))
    assert disabled.status == "disabled"
    assert disabled.version == 2
    reactivated = disabled.activate_as_member(occurred_at=NOW + timedelta(minutes=2))
    assert reactivated.status == "active"
    assert reactivated.membership_type == "member"
    assert reactivated.version == 3


def test_owner_and_non_active_member_cannot_use_invalid_transitions() -> None:
    with pytest.raises(InvalidMembershipTransitionError):
        membership(membership_type="owner").leave(occurred_at=NOW)
    with pytest.raises(InvalidMembershipTransitionError):
        membership(membership_type="owner").disable(occurred_at=NOW)
    with pytest.raises(InvalidMembershipTransitionError):
        membership(status="left").leave(occurred_at=NOW)
    with pytest.raises(InvalidMembershipTransitionError):
        membership().activate_as_member(occurred_at=NOW)


def test_invitation_is_targeted_single_use_and_expirable() -> None:
    invitation = WorkspaceInvitation(
        invitation_id=UUID("40000000-0000-4000-8000-000000000071"),
        workspace_id=WORKSPACE_ID,
        invited_account_id=MEMBER_ID,
        invited_by_account_id=OWNER_ID,
        status="pending",
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    accepted = invitation.accept(account_id=MEMBER_ID, occurred_at=NOW + timedelta(days=1))
    assert accepted.status == "accepted"
    assert accepted.accepted_at == NOW + timedelta(days=1)

    with pytest.raises(InvalidInvitationTransitionError):
        invitation.accept(account_id=OWNER_ID, occurred_at=NOW + timedelta(days=1))
    with pytest.raises(InvalidInvitationTransitionError):
        invitation.accept(account_id=MEMBER_ID, occurred_at=NOW + timedelta(days=8))
    with pytest.raises(InvalidInvitationTransitionError):
        accepted.accept(account_id=MEMBER_ID, occurred_at=NOW + timedelta(days=2))

    expired = invitation.expire(occurred_at=NOW + timedelta(days=8))
    assert expired.status == "expired"
    with pytest.raises(InvalidInvitationTransitionError):
        invitation.expire(occurred_at=NOW + timedelta(days=1))
