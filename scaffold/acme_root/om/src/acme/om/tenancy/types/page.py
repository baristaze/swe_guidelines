"""What a tenancy list answers with: one page, and whether another follows.
The manager asks storage for one row more than the page and keeps it out, so
`has_more` is a fact about the rows and not a guess about the count, the way
a task list answers."""

from acme.om.base import Platform
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.invitation import Invitation
from acme.om.tenancy.types.issued import OrgMembership
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.user import User


class UserPage(Platform):
    items: tuple[User, ...]
    has_more: bool


class ApiKeyPage(Platform):
    items: tuple[ApiKey, ...]
    has_more: bool


class MembershipPage(Platform):
    items: tuple[Membership, ...]
    has_more: bool


class OrgPage(Platform):
    items: tuple[Org, ...]
    has_more: bool


class OrgMembershipPage(Platform):
    """The memberships of one identity across tenants, by user id: what a
    sign-in lists, a page at a time for a person already signed in."""

    items: tuple[OrgMembership, ...]
    has_more: bool


class InvitationPage(Platform):
    items: tuple[Invitation, ...]
    has_more: bool


class OperatorTokenPage(Platform):
    """One operator's live tokens, newest first: rows of `sessions` of kind
    `operator_token`, never the token itself, which is kept as its digest."""

    items: tuple[Session, ...]
    has_more: bool
