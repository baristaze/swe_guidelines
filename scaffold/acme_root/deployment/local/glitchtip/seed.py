"""Seeds the local GlitchTip once it has migrated: an admin, an organization,
a team, the one project every Acme process reports to, with a fixed DSN
key so the DSNs in .env.example and the compose files work without clicking
through the UI, and a fixed read-only API token so the signal reader
(`acme-ops signals check --env local`) works the same way. Idempotent; run
by the glitchtip-seed one-shot service."""

import os
import uuid

from apps.api_tokens.models import APIToken
from apps.organizations_ext.constants import OrganizationUserRole
from apps.organizations_ext.models import Organization, OrganizationUser
from apps.projects.models import Project, ProjectKey
from apps.teams.models import Team
from apps.users.models import User

EMAIL = os.environ["GLITCHTIP_ADMIN_EMAIL"]
PASSWORD = os.environ["GLITCHTIP_ADMIN_PASSWORD"]
KEY = uuid.UUID(os.environ["GLITCHTIP_PROJECT_KEY"])
EXPECTED_DSN = os.environ["GLITCHTIP_EXPECTED_DSN"]
API_TOKEN = os.environ["GLITCHTIP_API_TOKEN"]
READ_SCOPES = ["project:read", "team:read", "event:read", "org:read", "member:read"]

user = User.objects.filter(email=EMAIL).first() or User.objects.create_superuser(
    email=EMAIL, password=PASSWORD
)
# GlitchTip makes a new organization's slug from its name, whatever slug it is
# created with. So the name is the slug the readers ask for, spelled the same.
org, _ = Organization.objects.get_or_create(slug="acme", defaults={"name": "acme"})
if not OrganizationUser.objects.filter(organization=org, user=user).exists():
    org.add_user(user, role=OrganizationUserRole.OWNER)
membership = OrganizationUser.objects.get(organization=org, user=user)
team, _ = Team.objects.get_or_create(organization=org, slug="acme")
team.members.add(membership)
project, _ = Project.objects.get_or_create(
    organization=org, slug="acme", defaults={"name": "acme", "platform": "python"}
)
team.projects.add(project)
if not ProjectKey.objects.filter(public_key=KEY).exists():
    # Saving a project creates a random key; the fixed one replaces it.
    ProjectKey.objects.filter(project=project).delete()
    ProjectKey.objects.create(project=project, public_key=KEY, name="local")

# The reader's token: the admin's, read scopes only, the value fixed the way
# the project key is. A token that can only read cannot break anything.
token, _ = APIToken.objects.get_or_create(
    token=API_TOKEN, defaults={"user": user, "label": "acme-ops (read)"}
)
token.add_permissions(READ_SCOPES)

dsn = ProjectKey.objects.get(public_key=KEY).get_dsn()
print(f"glitchtip seeded: {EMAIL}, DSN {dsn}, API token {API_TOKEN[:8]}... with read scopes")
if dsn != EXPECTED_DSN:
    raise SystemExit(f"DSN is {dsn}, expected {EXPECTED_DSN}: the project id moved")
