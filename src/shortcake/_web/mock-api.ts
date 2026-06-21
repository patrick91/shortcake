import type { Plugin } from 'vite';

const MOCK_STACK = {
  currentBranch: 'feat/notifications',
  branches: [
    {
      name: 'feat/auth',
      parent: 'main',
      depth: 0,
      isCurrent: false,
      commitCount: 3,
    },
    {
      name: 'feat/login-form',
      parent: 'feat/auth',
      depth: 1,
      isCurrent: false,
      commitCount: 2,
    },
    {
      name: 'feat/login-validation',
      parent: 'feat/login-form',
      depth: 2,
      isCurrent: false,
      commitCount: 1,
    },
    {
      name: 'feat/login-remember-me',
      parent: 'feat/login-form',
      depth: 2,
      isCurrent: false,
      commitCount: 1,
    },
    {
      name: 'feat/oauth',
      parent: 'feat/auth',
      depth: 1,
      isCurrent: false,
      commitCount: 1,
    },
    {
      name: 'feat/oauth-github',
      parent: 'feat/oauth',
      depth: 2,
      isCurrent: false,
      commitCount: 2,
    },
    {
      name: 'feat/oauth-github-orgs',
      parent: 'feat/oauth-github',
      depth: 3,
      isCurrent: false,
      commitCount: 1,
    },
    {
      name: 'feat/oauth-google',
      parent: 'feat/oauth',
      depth: 2,
      isCurrent: false,
      commitCount: 1,
    },
    {
      name: 'feat/notifications',
      parent: 'main',
      depth: 0,
      isCurrent: true,
      commitCount: 4,
    },
    {
      name: 'feat/email-alerts',
      parent: 'feat/notifications',
      depth: 1,
      isCurrent: false,
      commitCount: 1,
    },
    {
      name: 'feat/push-notifications',
      parent: 'feat/notifications',
      depth: 1,
      isCurrent: false,
      commitCount: 2,
    },
  ].map((branch, index) => {
    const commit = String(index + 1).repeat(40).slice(0, 40);
    const subjects = [
      'Add password session middleware',
      'Build login form shell',
      'Validate login payloads',
      'Persist remember-me preference',
      'Add OAuth provider abstraction',
      'Wire GitHub OAuth callback',
      'Restrict GitHub org access',
      'Add Google OAuth profile sync',
      'Create notification preference model',
      'Send email alert digest',
      'Register push notification devices',
    ];
    return {
      ...branch,
      commit,
      commitShort: commit.slice(0, 7),
      commitSubject: subjects[index] ?? 'Update branch',
    };
  }),
};

const MOCK_PATCHES: Record<string, { parent: string; patch: string }> = {
  'feat/auth': {
    parent: 'main',
    patch: `diff --git a/src/auth.py b/src/auth.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/auth.py
@@ -0,0 +1,25 @@
+from dataclasses import dataclass
+
+
+@dataclass
+class User:
+    id: int
+    username: str
+    email: str
+
+
+def authenticate(username: str, password: str) -> User | None:
+    """Authenticate a user with username and password."""
+    # TODO: implement real authentication
+    if username == "admin" and password == "secret":
+        return User(id=1, username="admin", email="admin@example.com")
+    return None
+
+
+def get_current_user(token: str) -> User | None:
+    """Look up user from session token."""
+    # TODO: implement token validation
+    return None
+
+
+__all__ = ["User", "authenticate", "get_current_user"]
diff --git a/src/middleware.py b/src/middleware.py
new file mode 100644
index 0000000..d4e5f6a
--- /dev/null
+++ b/src/middleware.py
@@ -0,0 +1,18 @@
+from functools import wraps
+from typing import Callable
+
+from .auth import get_current_user
+
+
+def require_auth(handler: Callable) -> Callable:
+    """Decorator that requires authentication."""
+    @wraps(handler)
+    def wrapper(request, *args, **kwargs):
+        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
+        user = get_current_user(token)
+        if user is None:
+            return {"error": "Unauthorized"}, 401
+        request.user = user
+        return handler(request, *args, **kwargs)
+    return wrapper
`,
  },
  'feat/login-form': {
    parent: 'feat/auth',
    patch: `diff --git a/src/templates/login.html b/src/templates/login.html
new file mode 100644
index 0000000..b2c3d4e
--- /dev/null
+++ b/src/templates/login.html
@@ -0,0 +1,15 @@
+<!DOCTYPE html>
+<html>
+<head><title>Login</title></head>
+<body>
+  <form method="POST" action="/login">
+    <label for="username">Username</label>
+    <input id="username" name="username" type="text" required />
+
+    <label for="password">Password</label>
+    <input id="password" name="password" type="password" required />
+
+    <button type="submit">Sign in</button>
+  </form>
+</body>
+</html>
`,
  },
  'feat/login-validation': {
    parent: 'feat/login-form',
    patch: `diff --git a/src/validation.py b/src/validation.py
new file mode 100644
index 0000000..1a2b3c4
--- /dev/null
+++ b/src/validation.py
@@ -0,0 +1,16 @@
+import re
+
+
+def validate_username(username: str) -> str | None:
+    if len(username) < 3:
+        return "Username must be at least 3 characters"
+    if not re.match(r"^[a-zA-Z0-9_]+$", username):
+        return "Username may only contain letters, numbers, and underscores"
+    return None
+
+
+def validate_password(password: str) -> str | None:
+    if len(password) < 8:
+        return "Password must be at least 8 characters"
+    if not re.search(r"[A-Z]", password):
+        return "Password must contain at least one uppercase letter"
+    return None
`,
  },
  'feat/login-remember-me': {
    parent: 'feat/login-form',
    patch: `diff --git a/src/session.py b/src/session.py
new file mode 100644
index 0000000..2b3c4d5
--- /dev/null
+++ b/src/session.py
@@ -0,0 +1,12 @@
+from datetime import timedelta
+
+DEFAULT_EXPIRY = timedelta(hours=1)
+REMEMBER_ME_EXPIRY = timedelta(days=30)
+
+
+def create_session(user_id: int, remember: bool = False) -> dict:
+    expiry = REMEMBER_ME_EXPIRY if remember else DEFAULT_EXPIRY
+    return {
+        "user_id": user_id,
+        "expires_in": int(expiry.total_seconds()),
+    }
`,
  },
  'feat/oauth': {
    parent: 'feat/auth',
    patch: `diff --git a/src/oauth.py b/src/oauth.py
new file mode 100644
index 0000000..c3d4e5f
--- /dev/null
+++ b/src/oauth.py
@@ -0,0 +1,12 @@
+"""OAuth2 provider integration."""
+
+PROVIDERS = {
+    "github": {
+        "authorize_url": "https://github.com/login/oauth/authorize",
+        "token_url": "https://github.com/login/oauth/access_token",
+    },
+}
+
+
+def get_authorize_url(provider: str, redirect_uri: str) -> str:
+    """Build the OAuth authorize redirect URL."""
+    config = PROVIDERS[provider]
+    return f"{config['authorize_url']}?redirect_uri={redirect_uri}"
`,
  },
  'feat/oauth-github': {
    parent: 'feat/oauth',
    patch: `diff --git a/src/oauth_github.py b/src/oauth_github.py
new file mode 100644
index 0000000..3c4d5e6
--- /dev/null
+++ b/src/oauth_github.py
@@ -0,0 +1,18 @@
+import httpx
+
+GITHUB_API = "https://api.github.com"
+
+
+async def exchange_code(code: str, client_id: str, client_secret: str) -> str:
+    async with httpx.AsyncClient() as client:
+        resp = await client.post(
+            "https://github.com/login/oauth/access_token",
+            json={"client_id": client_id, "client_secret": client_secret, "code": code},
+            headers={"Accept": "application/json"},
+        )
+        return resp.json()["access_token"]
+
+
+async def get_github_user(token: str) -> dict:
+    async with httpx.AsyncClient() as client:
+        resp = await client.get(f"{GITHUB_API}/user", headers={"Authorization": f"Bearer {token}"})
+        return resp.json()
`,
  },
  'feat/oauth-github-orgs': {
    parent: 'feat/oauth-github',
    patch: `diff --git a/src/oauth_github_orgs.py b/src/oauth_github_orgs.py
new file mode 100644
index 0000000..4d5e6f7
--- /dev/null
+++ b/src/oauth_github_orgs.py
@@ -0,0 +1,14 @@
+import httpx
+
+GITHUB_API = "https://api.github.com"
+
+
+async def get_user_orgs(token: str) -> list[dict]:
+    async with httpx.AsyncClient() as client:
+        resp = await client.get(f"{GITHUB_API}/user/orgs", headers={"Authorization": f"Bearer {token}"})
+        return resp.json()
+
+
+async def check_org_membership(token: str, org: str) -> bool:
+    orgs = await get_user_orgs(token)
+    return any(o["login"] == org for o in orgs)
`,
  },
  'feat/oauth-google': {
    parent: 'feat/oauth',
    patch: `diff --git a/src/oauth_google.py b/src/oauth_google.py
new file mode 100644
index 0000000..5e6f7a8
--- /dev/null
+++ b/src/oauth_google.py
@@ -0,0 +1,15 @@
+import httpx
+
+GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
+GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
+
+
+async def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
+    async with httpx.AsyncClient() as client:
+        resp = await client.post(GOOGLE_TOKEN_URL, data={
+            "code": code, "client_id": client_id,
+            "client_secret": client_secret, "redirect_uri": redirect_uri,
+            "grant_type": "authorization_code",
+        })
+        return resp.json()["access_token"]
`,
  },
  'feat/notifications': {
    parent: 'main',
    patch: `diff --git a/src/notifications.py b/src/notifications.py
new file mode 100644
index 0000000..e5f6a7b
--- /dev/null
+++ b/src/notifications.py
@@ -0,0 +1,30 @@
+from dataclasses import dataclass, field
+from datetime import datetime
+from enum import Enum
+
+
+class NotificationType(Enum):
+    INFO = "info"
+    WARNING = "warning"
+    ERROR = "error"
+
+
+@dataclass
+class Notification:
+    message: str
+    type: NotificationType = NotificationType.INFO
+    read: bool = False
+    created_at: datetime = field(default_factory=datetime.now)
+
+
+class NotificationService:
+    def __init__(self) -> None:
+        self._notifications: list[Notification] = []
+
+    def send(self, message: str, type: NotificationType = NotificationType.INFO) -> None:
+        self._notifications.append(Notification(message=message, type=type))
+
+    def unread(self) -> list[Notification]:
+        return [n for n in self._notifications if not n.read]
+
+    def mark_all_read(self) -> None:
+        for n in self._notifications:
+            n.read = True
`,
  },
  'feat/email-alerts': {
    parent: 'feat/notifications',
    patch: `diff --git a/src/email_alerts.py b/src/email_alerts.py
new file mode 100644
index 0000000..f6a7b8c
--- /dev/null
+++ b/src/email_alerts.py
@@ -0,0 +1,10 @@
+from .notifications import Notification, NotificationService
+
+
+class EmailAlertService:
+    def __init__(self, notification_service: NotificationService) -> None:
+        self._service = notification_service
+
+    def send_digest(self, recipient: str) -> int:
+        unread = self._service.unread()
+        # TODO: send email with unread notifications
+        return len(unread)
`,
  },
  'feat/push-notifications': {
    parent: 'feat/notifications',
    patch: `diff --git a/src/push.py b/src/push.py
new file mode 100644
index 0000000..6f7a8b9
--- /dev/null
+++ b/src/push.py
@@ -0,0 +1,19 @@
+from dataclasses import dataclass
+
+from .notifications import Notification, NotificationService
+
+
+@dataclass
+class PushSubscription:
+    endpoint: str
+    user_id: int
+
+
+class PushNotificationService:
+    def __init__(self, notification_service: NotificationService) -> None:
+        self._service = notification_service
+        self._subscriptions: list[PushSubscription] = []
+
+    def subscribe(self, endpoint: str, user_id: int) -> None:
+        self._subscriptions.append(PushSubscription(endpoint=endpoint, user_id=user_id))
+
+    def broadcast(self, message: str) -> int:
+        # TODO: send push to all subscribed endpoints
+        return len(self._subscriptions)
`,
  },
};

// --- Large working changes mock for performance testing ---

function normalizeMockPatch(patch: string): string {
  const lines = patch.split('\n');
  let inHunk = false;

  return lines
    .map((line, index) => {
      if (line.startsWith('diff --git ')) {
        inHunk = false;
        return line;
      }
      if (line.startsWith('@@')) {
        inHunk = true;
        return line;
      }
      if (
        inHunk &&
        line === '' &&
        index < lines.length - 1 &&
        !lines[index + 1]?.startsWith('diff --git ')
      ) {
        return ' ';
      }
      return line;
    })
    .join('\n');
}

function generateWorkingChangesPatch(): string {
  const files: string[] = [];

  // File 1: Large config file with multiple scattered changes
  files.push(`diff --git a/src/config/settings.py b/src/config/settings.py
index 1a2b3c4..5e6f7a8 100644
--- a/src/config/settings.py
+++ b/src/config/settings.py
@@ -1,6 +1,8 @@
 import os
+import logging
 from pathlib import Path

+logger = logging.getLogger(__name__)

 BASE_DIR = Path(__file__).resolve().parent.parent

@@ -15,7 +17,9 @@
 DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///db.sqlite3")
 REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

-ALLOWED_HOSTS = ["localhost"]
+ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]
+CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]
+CSRF_TRUSTED_ORIGINS = ["http://localhost:3000"]

 INSTALLED_APPS = [
     "django.contrib.admin",
@@ -30,6 +34,12 @@
     "django.contrib.staticfiles",
 ]

+MIDDLEWARE = [
+    "django.middleware.security.SecurityMiddleware",
+    "corsheaders.middleware.CorsMiddleware",
+    "django.middleware.common.CommonMiddleware",
+    "django.middleware.csrf.CsrfViewMiddleware",
+]

 # Email settings
 EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")`);

  // File 2: New API router
  files.push(`diff --git a/src/api/router.py b/src/api/router.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/api/router.py
@@ -0,0 +1,65 @@
+from fastapi import APIRouter, Depends, HTTPException, status
+from typing import Annotated
+
+from ..auth import get_current_user, User
+from ..models import Project, Task
+from ..schemas import ProjectCreate, ProjectUpdate, TaskCreate, TaskUpdate
+from ..database import get_db
+
+router = APIRouter(prefix="/api/v1", tags=["projects"])
+
+
+@router.get("/projects")
+async def list_projects(
+    user: Annotated[User, Depends(get_current_user)],
+    db=Depends(get_db),
+):
+    return await db.fetch_all(
+        Project.select().where(Project.owner_id == user.id)
+    )
+
+
+@router.post("/projects", status_code=status.HTTP_201_CREATED)
+async def create_project(
+    data: ProjectCreate,
+    user: Annotated[User, Depends(get_current_user)],
+    db=Depends(get_db),
+):
+    project = await db.execute(
+        Project.insert().values(**data.model_dump(), owner_id=user.id)
+    )
+    return {"id": project, **data.model_dump()}
+
+
+@router.get("/projects/{project_id}")
+async def get_project(
+    project_id: int,
+    user: Annotated[User, Depends(get_current_user)],
+    db=Depends(get_db),
+):
+    project = await db.fetch_one(
+        Project.select().where(
+            (Project.id == project_id) & (Project.owner_id == user.id)
+        )
+    )
+    if not project:
+        raise HTTPException(status_code=404, detail="Project not found")
+    return project
+
+
+@router.put("/projects/{project_id}")
+async def update_project(
+    project_id: int,
+    data: ProjectUpdate,
+    user: Annotated[User, Depends(get_current_user)],
+    db=Depends(get_db),
+):
+    result = await db.execute(
+        Project.update()
+        .where((Project.id == project_id) & (Project.owner_id == user.id))
+        .values(**data.model_dump(exclude_unset=True))
+    )
+    if result == 0:
+        raise HTTPException(status_code=404, detail="Project not found")
+    return await get_project(project_id, user, db)`);

  // File 3: Database models with modifications
  files.push(`diff --git a/src/models/project.py b/src/models/project.py
index 2b3c4d5..6f7a8b9 100644
--- a/src/models/project.py
+++ b/src/models/project.py
@@ -1,15 +1,28 @@
-from sqlalchemy import Column, Integer, String, DateTime
+from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey
+from sqlalchemy.orm import relationship
 from datetime import datetime

 from .base import Base


 class Project(Base):
     __tablename__ = "projects"

     id = Column(Integer, primary_key=True)
-    name = Column(String(100), nullable=False)
+    name = Column(String(200), nullable=False, index=True)
+    slug = Column(String(200), nullable=False, unique=True)
+    description = Column(Text, nullable=True)
+    is_archived = Column(Boolean, default=False, nullable=False)
+    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
     created_at = Column(DateTime, default=datetime.utcnow)
+    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
+
+    owner = relationship("User", back_populates="projects")
+    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
+
+    def __repr__(self) -> str:
+        return f"<Project {self.slug}>"
+
+    @property
+    def is_active(self) -> bool:
+        return not self.is_archived`);

  // File 4: New task model
  files.push(`diff --git a/src/models/task.py b/src/models/task.py
new file mode 100644
index 0000000..3c4d5e6
--- /dev/null
+++ b/src/models/task.py
@@ -0,0 +1,45 @@
+from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, Enum
+from sqlalchemy.orm import relationship
+from datetime import datetime
+import enum
+
+from .base import Base
+
+
+class TaskStatus(enum.Enum):
+    TODO = "todo"
+    IN_PROGRESS = "in_progress"
+    IN_REVIEW = "in_review"
+    DONE = "done"
+    CANCELLED = "cancelled"
+
+
+class TaskPriority(enum.Enum):
+    LOW = "low"
+    MEDIUM = "medium"
+    HIGH = "high"
+    CRITICAL = "critical"
+
+
+class Task(Base):
+    __tablename__ = "tasks"
+
+    id = Column(Integer, primary_key=True)
+    title = Column(String(300), nullable=False)
+    description = Column(Text, nullable=True)
+    status = Column(Enum(TaskStatus), default=TaskStatus.TODO, nullable=False)
+    priority = Column(Enum(TaskPriority), default=TaskPriority.MEDIUM, nullable=False)
+    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
+    assignee_id = Column(Integer, ForeignKey("users.id"), nullable=True)
+    created_at = Column(DateTime, default=datetime.utcnow)
+    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
+    due_date = Column(DateTime, nullable=True)
+    completed_at = Column(DateTime, nullable=True)
+
+    project = relationship("Project", back_populates="tasks")
+    assignee = relationship("User", back_populates="assigned_tasks")
+
+    @property
+    def is_overdue(self) -> bool:
+        if self.due_date and self.status != TaskStatus.DONE:
+            return datetime.utcnow() > self.due_date
+        return False`);

  // File 5: Pydantic schemas
  files.push(`diff --git a/src/schemas/project.py b/src/schemas/project.py
new file mode 100644
index 0000000..4d5e6f7
--- /dev/null
+++ b/src/schemas/project.py
@@ -0,0 +1,38 @@
+from pydantic import BaseModel, Field
+from datetime import datetime
+
+
+class ProjectCreate(BaseModel):
+    name: str = Field(..., min_length=1, max_length=200)
+    slug: str = Field(..., min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
+    description: str | None = None
+
+
+class ProjectUpdate(BaseModel):
+    name: str | None = Field(None, min_length=1, max_length=200)
+    description: str | None = None
+    is_archived: bool | None = None
+
+
+class ProjectResponse(BaseModel):
+    id: int
+    name: str
+    slug: str
+    description: str | None
+    is_archived: bool
+    owner_id: int
+    created_at: datetime
+    updated_at: datetime
+
+    model_config = {"from_attributes": True}
+
+
+class TaskCreate(BaseModel):
+    title: str = Field(..., min_length=1, max_length=300)
+    description: str | None = None
+    priority: str = "medium"
+    assignee_id: int | None = None
+    due_date: datetime | None = None
+
+
+class TaskUpdate(BaseModel):
+    title: str | None = Field(None, min_length=1, max_length=300)
+    status: str | None = None
+    priority: str | None = None
+    assignee_id: int | None = None
+    due_date: datetime | None = None`);

  // File 6: Test file with many test functions
  files.push(`diff --git a/tests/test_api_projects.py b/tests/test_api_projects.py
new file mode 100644
index 0000000..5e6f7a8
--- /dev/null
+++ b/tests/test_api_projects.py
@@ -0,0 +1,82 @@
+import pytest
+from httpx import AsyncClient
+
+from src.models import Project
+
+
+@pytest.fixture
+async def auth_client(client: AsyncClient, test_user):
+    client.headers["Authorization"] = f"Bearer {test_user.token}"
+    return client
+
+
+@pytest.fixture
+async def sample_project(auth_client: AsyncClient):
+    resp = await auth_client.post("/api/v1/projects", json={
+        "name": "Test Project",
+        "slug": "test-project",
+        "description": "A test project",
+    })
+    return resp.json()
+
+
+class TestListProjects:
+    async def test_empty_list(self, auth_client):
+        resp = await auth_client.get("/api/v1/projects")
+        assert resp.status_code == 200
+        assert resp.json() == []
+
+    async def test_returns_own_projects(self, auth_client, sample_project):
+        resp = await auth_client.get("/api/v1/projects")
+        assert resp.status_code == 200
+        assert len(resp.json()) == 1
+        assert resp.json()[0]["slug"] == "test-project"
+
+    async def test_unauthenticated(self, client):
+        resp = await client.get("/api/v1/projects")
+        assert resp.status_code == 401
+
+
+class TestCreateProject:
+    async def test_create_success(self, auth_client):
+        resp = await auth_client.post("/api/v1/projects", json={
+            "name": "New Project",
+            "slug": "new-project",
+        })
+        assert resp.status_code == 201
+        assert resp.json()["name"] == "New Project"
+
+    async def test_duplicate_slug(self, auth_client, sample_project):
+        resp = await auth_client.post("/api/v1/projects", json={
+            "name": "Duplicate",
+            "slug": "test-project",
+        })
+        assert resp.status_code == 409
+
+    async def test_invalid_slug(self, auth_client):
+        resp = await auth_client.post("/api/v1/projects", json={
+            "name": "Bad Slug",
+            "slug": "Bad Slug!",
+        })
+        assert resp.status_code == 422
+
+
+class TestGetProject:
+    async def test_get_success(self, auth_client, sample_project):
+        pid = sample_project["id"]
+        resp = await auth_client.get(f"/api/v1/projects/{pid}")
+        assert resp.status_code == 200
+        assert resp.json()["slug"] == "test-project"
+
+    async def test_not_found(self, auth_client):
+        resp = await auth_client.get("/api/v1/projects/999")
+        assert resp.status_code == 404
+
+
+class TestUpdateProject:
+    async def test_update_name(self, auth_client, sample_project):
+        pid = sample_project["id"]
+        resp = await auth_client.put(f"/api/v1/projects/{pid}", json={
+            "name": "Updated Name",
+        })
+        assert resp.status_code == 200
+        assert resp.json()["name"] == "Updated Name"
+
+    async def test_archive_project(self, auth_client, sample_project):
+        pid = sample_project["id"]
+        resp = await auth_client.put(f"/api/v1/projects/{pid}", json={
+            "is_archived": True,
+        })
+        assert resp.status_code == 200
+        assert resp.json()["is_archived"] is True`);

  // File 7: Database migrations
  files.push(`diff --git a/src/database/migrations/002_add_projects.py b/src/database/migrations/002_add_projects.py
new file mode 100644
index 0000000..6f7a8b9
--- /dev/null
+++ b/src/database/migrations/002_add_projects.py
@@ -0,0 +1,35 @@
+"""Add projects and tasks tables."""
+
+from alembic import op
+import sqlalchemy as sa
+
+
+revision = "002"
+down_revision = "001"
+
+
+def upgrade() -> None:
+    op.create_table(
+        "projects",
+        sa.Column("id", sa.Integer(), primary_key=True),
+        sa.Column("name", sa.String(200), nullable=False),
+        sa.Column("slug", sa.String(200), nullable=False, unique=True),
+        sa.Column("description", sa.Text(), nullable=True),
+        sa.Column("is_archived", sa.Boolean(), default=False),
+        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id")),
+        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
+        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
+    )
+    op.create_index("ix_projects_slug", "projects", ["slug"])
+    op.create_index("ix_projects_owner", "projects", ["owner_id"])
+
+    op.create_table(
+        "tasks",
+        sa.Column("id", sa.Integer(), primary_key=True),
+        sa.Column("title", sa.String(300), nullable=False),
+        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
+        sa.Column("status", sa.String(20), default="todo"),
+        sa.Column("priority", sa.String(20), default="medium"),
+    )
+
+
+def downgrade() -> None:
+    op.drop_table("tasks")
+    op.drop_table("projects")`);

  // File 8: Utility functions
  files.push(`diff --git a/src/utils/slugify.py b/src/utils/slugify.py
new file mode 100644
index 0000000..7a8b9c0
--- /dev/null
+++ b/src/utils/slugify.py
@@ -0,0 +1,22 @@
+import re
+import unicodedata
+
+
+def slugify(value: str, max_length: int = 200) -> str:
+    """Convert a string to a URL-friendly slug."""
+    value = unicodedata.normalize("NFKD", value)
+    value = value.encode("ascii", "ignore").decode("ascii")
+    value = re.sub(r"[^\w\s-]", "", value.lower())
+    value = re.sub(r"[-\s]+", "-", value).strip("-")
+    return value[:max_length]
+
+
+def unique_slug(base: str, existing: set[str], max_length: int = 200) -> str:
+    """Generate a unique slug by appending a counter if needed."""
+    slug = slugify(base, max_length)
+    if slug not in existing:
+        return slug
+    counter = 1
+    while f"{slug}-{counter}" in existing:
+        counter += 1
+    return f"{slug}-{counter}"`);

  // File 9: Logging configuration changes
  files.push(`diff --git a/src/config/logging.py b/src/config/logging.py
index 8b9c0d1..2e3f4a5 100644
--- a/src/config/logging.py
+++ b/src/config/logging.py
@@ -1,8 +1,15 @@
 import logging
+import logging.handlers
+import sys
+from pathlib import Path


-def setup_logging(level: str = "INFO") -> None:
-    logging.basicConfig(level=level)
+LOG_DIR = Path("logs")
+
+
+def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
+    LOG_DIR.mkdir(exist_ok=True)
+
     root = logging.getLogger()
     root.setLevel(level)

@@ -11,4 +18,18 @@
         "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
     )

-    root.handlers[0].setFormatter(formatter)
+    console = logging.StreamHandler(sys.stdout)
+    console.setFormatter(formatter)
+    root.addHandler(console)
+
+    if log_file:
+        file_handler = logging.handlers.RotatingFileHandler(
+            LOG_DIR / log_file,
+            maxBytes=10 * 1024 * 1024,
+            backupCount=5,
+        )
+        file_handler.setFormatter(formatter)
+        root.addHandler(file_handler)
+
+    # Silence noisy third-party loggers
+    logging.getLogger("httpx").setLevel(logging.WARNING)
+    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)`);

  // File 10: New CLI commands
  files.push(`diff --git a/src/cli/commands.py b/src/cli/commands.py
new file mode 100644
index 0000000..9c0d1e2
--- /dev/null
+++ b/src/cli/commands.py
@@ -0,0 +1,48 @@
+import typer
+from rich.console import Console
+from rich.table import Table
+
+from ..database import get_sync_db
+from ..models import Project, Task, TaskStatus
+
+app = typer.Typer(help="Project management CLI")
+console = Console()
+
+
+@app.command()
+def list_projects(archived: bool = False):
+    """List all projects."""
+    db = get_sync_db()
+    query = Project.select()
+    if not archived:
+        query = query.where(Project.is_archived == False)
+
+    projects = db.fetch_all(query)
+    table = Table(title="Projects")
+    table.add_column("ID", style="dim")
+    table.add_column("Name", style="bold")
+    table.add_column("Slug")
+    table.add_column("Tasks", justify="right")
+    table.add_column("Status")
+
+    for p in projects:
+        task_count = db.count(Task.select().where(Task.project_id == p.id))
+        status = "[red]Archived[/]" if p.is_archived else "[green]Active[/]"
+        table.add_row(str(p.id), p.name, p.slug, str(task_count), status)
+
+    console.print(table)
+
+
+@app.command()
+def create_project(name: str, slug: str = None, description: str = None):
+    """Create a new project."""
+    from ..utils.slugify import slugify
+    db = get_sync_db()
+    if slug is None:
+        slug = slugify(name)
+    project_id = db.execute(
+        Project.insert().values(name=name, slug=slug, description=description)
+    )
+    console.print(f"[green]Created project '{name}' (id={project_id})[/]")
+
+
+@app.command()
+def project_stats():
+    """Show project statistics."""
+    db = get_sync_db()
+    total = db.count(Project.select())
+    active = db.count(Project.select().where(Project.is_archived == False))
+    console.print(f"Total: {total}, Active: {active}, Archived: {total - active}")`);

  // File 11: TypeScript frontend component
  files.push(`diff --git a/frontend/src/components/ProjectList.tsx b/frontend/src/components/ProjectList.tsx
new file mode 100644
index 0000000..0d1e2f3
--- /dev/null
+++ b/frontend/src/components/ProjectList.tsx
@@ -0,0 +1,52 @@
+import { useEffect, useState } from 'react';
+
+interface Project {
+  id: number;
+  name: string;
+  slug: string;
+  description: string | null;
+  is_archived: boolean;
+  created_at: string;
+}
+
+export function ProjectList() {
+  const [projects, setProjects] = useState<Project[]>([]);
+  const [loading, setLoading] = useState(true);
+  const [error, setError] = useState<string | null>(null);
+
+  useEffect(() => {
+    fetch('/api/v1/projects')
+      .then((res) => {
+        if (!res.ok) throw new Error('Failed to fetch projects');
+        return res.json();
+      })
+      .then(setProjects)
+      .catch((err) => setError(err.message))
+      .finally(() => setLoading(false));
+  }, []);
+
+  if (loading) return <div className="animate-pulse">Loading projects...</div>;
+  if (error) return <div className="text-red-500">Error: {error}</div>;
+
+  return (
+    <div className="space-y-4">
+      <h2 className="text-xl font-bold">Projects</h2>
+      {projects.length === 0 ? (
+        <p className="text-gray-500">No projects yet. Create your first one!</p>
+      ) : (
+        <ul className="divide-y">
+          {projects.map((project) => (
+            <li key={project.id} className="py-3 flex items-center justify-between">
+              <div>
+                <h3 className="font-medium">{project.name}</h3>
+                <p className="text-sm text-gray-500">{project.slug}</p>
+                {project.description && (
+                  <p className="text-sm mt-1">{project.description}</p>
+                )}
+              </div>
+              {project.is_archived && (
+                <span className="text-xs bg-yellow-100 text-yellow-800 px-2 py-0.5 rounded">
+                  Archived
+                </span>
+              )}
+            </li>
+          ))}
+        </ul>
+      )}
+    </div>
+  );
+}`);

  // File 12: CSS module changes
  files.push(`diff --git a/frontend/src/styles/projects.css b/frontend/src/styles/projects.css
new file mode 100644
index 0000000..1e2f3a4
--- /dev/null
+++ b/frontend/src/styles/projects.css
@@ -0,0 +1,28 @@
+.project-card {
+  border: 1px solid var(--border-color);
+  border-radius: 8px;
+  padding: 16px;
+  transition: box-shadow 0.2s ease;
+}
+
+.project-card:hover {
+  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
+}
+
+.project-card__title {
+  font-size: 1.125rem;
+  font-weight: 600;
+  margin-bottom: 4px;
+}
+
+.project-card__slug {
+  font-family: monospace;
+  font-size: 0.75rem;
+  color: var(--text-muted);
+}
+
+.project-card__badge {
+  font-size: 0.625rem;
+  padding: 2px 6px;
+  border-radius: 9999px;
+  text-transform: uppercase;
+}`);

  // File 13: Docker configuration
  files.push(`diff --git a/docker-compose.yml b/docker-compose.yml
index 3f4a5b6..7c8d9e0 100644
--- a/docker-compose.yml
+++ b/docker-compose.yml
@@ -1,11 +1,32 @@
-version: "3.8"
 services:
   app:
     build: .
-    ports:
-      - "8000:8000"
+    ports: ["8000:8000"]
+    depends_on:
+      db:
+        condition: service_healthy
+      redis:
+        condition: service_started
     environment:
-      - DATABASE_URL=sqlite:///db.sqlite3
+      DATABASE_URL: postgres://app:secret@db:5432/shortcake
+      REDIS_URL: redis://redis:6379/0
+      LOG_LEVEL: INFO
+    volumes:
+      - ./logs:/app/logs
+    restart: unless-stopped
+
+  db:
+    image: postgres:16-alpine
+    environment:
+      POSTGRES_USER: app
+      POSTGRES_PASSWORD: secret
+      POSTGRES_DB: shortcake
+    volumes:
+      - pgdata:/var/lib/postgresql/data
+    healthcheck:
+      test: pg_isready -U app
+      interval: 5s
+      retries: 5
+
+  redis:
+    image: redis:7-alpine
+    command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru

 volumes:
-  data:
+  pgdata:`);

  // File 14: README changes
  files.push(`diff --git a/README.md b/README.md
index 4a5b6c7..8d9e0f1 100644
--- a/README.md
+++ b/README.md
@@ -1,8 +1,16 @@
 # Shortcake

-A simple project template.
+A stacked PR workflow tool using git trailers.

 ## Getting Started

-1. Clone the repo
-2. Run the app
+1. Install dependencies: \`pip install -e ".[dev]"\`
+2. Run migrations: \`alembic upgrade head\`
+3. Start the server: \`uvicorn src.main:app --reload\`
+4. Start the frontend: \`cd frontend && npm run dev\`
+
+## Development
+
+- Run tests: \`pytest\`
+- Format code: \`ruff format .\`
+- Type check: \`mypy src/\``);

  // File 15: GitHub Actions workflow
  files.push(`diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
new file mode 100644
index 0000000..2f3a4b5
--- /dev/null
+++ b/.github/workflows/ci.yml
@@ -0,0 +1,42 @@
+name: CI
+on:
+  push:
+    branches: [main]
+  pull_request:
+    branches: [main]
+
+jobs:
+  test:
+    runs-on: ubuntu-latest
+    services:
+      postgres:
+        image: postgres:16-alpine
+        env:
+          POSTGRES_USER: test
+          POSTGRES_PASSWORD: test
+          POSTGRES_DB: test_db
+        options: --health-cmd pg_isready --health-interval 5s --health-retries 5
+        ports: ["5432:5432"]
+    steps:
+      - uses: actions/checkout@v4
+      - uses: actions/setup-python@v5
+        with:
+          python-version: "3.14"
+      - run: pip install -e ".[dev]"
+      - run: pytest --cov --cov-report=xml
+        env:
+          DATABASE_URL: postgres://test:test@localhost:5432/test_db
+      - uses: codecov/codecov-action@v4
+
+  lint:
+    runs-on: ubuntu-latest
+    steps:
+      - uses: actions/checkout@v4
+      - uses: actions/setup-python@v5
+        with:
+          python-version: "3.14"
+      - run: pip install ruff mypy
+      - run: ruff check .
+      - run: ruff format --check .
+      - run: mypy src/`);

  // Generate many more files programmatically for stress testing
  const modules = [
    'cache', 'permissions', 'rate_limiter', 'analytics', 'search',
    'billing', 'webhooks', 'export', 'import_data', 'audit_log',
    'health_check', 'feature_flags', 'i18n', 'file_storage', 'queue',
    'scheduler', 'metrics', 'middleware_chain', 'pagination', 'serializers',
  ];

  for (const mod of modules) {
    const lines: string[] = [];
    const lineCount = 30 + Math.floor(mod.length * 7); // vary size per module
    lines.push(`diff --git a/src/services/${mod}.py b/src/services/${mod}.py`);
    lines.push('new file mode 100644');
    lines.push('index 0000000..abcdef1');
    lines.push('--- /dev/null');
    lines.push(`+++ b/src/services/${mod}.py`);
    lines.push(`@@ -0,0 +1,${lineCount} @@`);
    lines.push(`+"""${mod.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())} service module."""`);
    lines.push('+');
    lines.push('+from __future__ import annotations');
    lines.push('+from dataclasses import dataclass, field');
    lines.push('+from datetime import datetime, timedelta');
    lines.push('+from typing import Any, Protocol');
    lines.push('+import logging');
    lines.push('+');
    lines.push(`+logger = logging.getLogger("${mod}")`);
    lines.push('+');
    lines.push('+');
    lines.push(`+class ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Error(Exception):`);
    lines.push(`+    """Raised when ${mod.replace(/_/g, ' ')} operation fails."""`);
    lines.push('+');
    lines.push('+');
    lines.push('+@dataclass');
    lines.push(`+class ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Config:`);
    lines.push('+    enabled: bool = True');
    lines.push('+    timeout: int = 30');
    lines.push('+    max_retries: int = 3');
    lines.push(`+    namespace: str = "${mod}"`);
    lines.push('+    metadata: dict[str, Any] = field(default_factory=dict)');
    lines.push('+');
    lines.push('+');
    lines.push('+class ServiceProtocol(Protocol):');
    lines.push('+    def initialize(self) -> None: ...');
    lines.push('+    def shutdown(self) -> None: ...');
    lines.push('+    def health_check(self) -> bool: ...');
    lines.push('+');
    lines.push('+');
    lines.push(`+class ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Service:`);
    lines.push(`+    """Main ${mod.replace(/_/g, ' ')} service implementation."""`);
    lines.push('+');
    lines.push(`+    def __init__(self, config: ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Config | None = None) -> None:`);
    lines.push(`+        self.config = config or ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Config()`);
    lines.push('+        self._initialized = False');
    lines.push('+        self._data: dict[str, Any] = {}');
    lines.push(`+        logger.info("${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Service created")`);
    lines.push('+');
    lines.push('+    def initialize(self) -> None:');
    lines.push('+        if self._initialized:');
    lines.push('+            return');
    lines.push('+        self._initialized = True');
    lines.push(`+        logger.info("${mod} service initialized")`);
    lines.push('+');
    lines.push('+    def shutdown(self) -> None:');
    lines.push('+        self._initialized = False');
    lines.push('+        self._data.clear()');
    lines.push(`+        logger.info("${mod} service shut down")`);
    lines.push('+');
    lines.push('+    def health_check(self) -> bool:');
    lines.push('+        return self._initialized');
    lines.push('+');
    lines.push(`+    def process(self, key: str, value: Any) -> dict[str, Any]:`);
    lines.push('+        if not self._initialized:');
    lines.push(`+            raise ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Error("Service not initialized")`);
    lines.push('+        self._data[key] = value');
    lines.push('+        return {"key": key, "status": "processed", "timestamp": datetime.utcnow().isoformat()}');
    lines.push('+');
    lines.push('+    def get(self, key: str) -> Any | None:');
    lines.push('+        return self._data.get(key)');
    lines.push('+');
    lines.push('+    def delete(self, key: str) -> bool:');
    lines.push('+        if key in self._data:');
    lines.push('+            del self._data[key]');
    lines.push('+            return True');
    lines.push('+        return False');
    lines.push('+');
    lines.push('+    def list_keys(self) -> list[str]:');
    lines.push('+        return list(self._data.keys())');
    lines.push('+');
    lines.push('+    @property');
    lines.push('+    def size(self) -> int:');
    lines.push('+        return len(self._data)');

    // Add test file for each module too
    const testLines: string[] = [];
    testLines.push(`diff --git a/tests/test_${mod}.py b/tests/test_${mod}.py`);
    testLines.push('new file mode 100644');
    testLines.push('index 0000000..fedcba9');
    testLines.push('--- /dev/null');
    testLines.push(`+++ b/tests/test_${mod}.py`);
    testLines.push(`@@ -0,0 +1,35 @@`);
    testLines.push('+import pytest');
    testLines.push(`+from src.services.${mod} import ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Service, ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Config`);
    testLines.push('+');
    testLines.push('+');
    testLines.push('+@pytest.fixture');
    testLines.push('+def service():');
    testLines.push(`+    svc = ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Service()`);
    testLines.push('+    svc.initialize()');
    testLines.push('+    yield svc');
    testLines.push('+    svc.shutdown()');
    testLines.push('+');
    testLines.push('+');
    testLines.push('+def test_initialize(service):');
    testLines.push('+    assert service.health_check() is True');
    testLines.push('+');
    testLines.push('+');
    testLines.push('+def test_process(service):');
    testLines.push('+    result = service.process("key1", {"value": 42})');
    testLines.push('+    assert result["status"] == "processed"');
    testLines.push('+    assert service.get("key1") == {"value": 42}');
    testLines.push('+');
    testLines.push('+');
    testLines.push('+def test_delete(service):');
    testLines.push('+    service.process("key1", "val")');
    testLines.push('+    assert service.delete("key1") is True');
    testLines.push('+    assert service.delete("key1") is False');
    testLines.push('+');
    testLines.push('+');
    testLines.push('+def test_list_keys(service):');
    testLines.push('+    service.process("a", 1)');
    testLines.push('+    service.process("b", 2)');
    testLines.push('+    assert sorted(service.list_keys()) == ["a", "b"]');
    testLines.push('+');
    testLines.push('+');
    testLines.push('+def test_not_initialized():');
    testLines.push(`+    svc = ${mod.split('_').map(w => w[0]!.toUpperCase() + w.slice(1)).join('')}Service()`);
    testLines.push('+    with pytest.raises(Exception):');
    testLines.push('+        svc.process("key", "value")');

    files.push(lines.join('\n'));
    files.push(testLines.join('\n'));
  }

  // Add a massive generated file to test large-file gating
  const bigLines: string[] = [];
  const bigCount = 800;
  bigLines.push('diff --git a/src/generated/schemas.gen.ts b/src/generated/schemas.gen.ts');
  bigLines.push('new file mode 100644');
  bigLines.push('index 0000000..1234567');
  bigLines.push('--- /dev/null');
  bigLines.push('+++ b/src/generated/schemas.gen.ts');
  bigLines.push(`@@ -0,0 +1,${bigCount} @@`);
  bigLines.push('+// Auto-generated — do not edit');
  bigLines.push('+/* eslint-disable */');
  bigLines.push('+export const schemas = {');
  for (let i = 0; i < bigCount - 5; i++) {
    const name = `field_${String(i).padStart(4, '0')}`;
    bigLines.push(`+  ${name}: { type: "string", description: "Generated field ${i}", required: ${i % 3 === 0} },`);
  }
  bigLines.push('+} as const;');
  bigLines.push('+export type Schemas = typeof schemas;');
  files.push(bigLines.join('\n'));

  return files.join('\n');
}

const MOCK_WORKING_PATCH = normalizeMockPatch(generateWorkingChangesPatch());
const MOCK_WORKING_DIFF_KEY = '0'.repeat(64);
let mockReviewState = {
  version: 1,
  diffStyle: 'unified',
  viewedFiles: {} as Record<string, Record<string, string>>,
};

function json(res: import('http').ServerResponse, status: number, data: unknown) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

export function mockApi(): Plugin {
  return {
    name: 'mock-api',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = new URL(req.url ?? '/', `http://${req.headers.host}`);

        if (url.pathname === '/api/health') {
          return json(res, 200, { ok: true });
        }

        if (url.pathname === '/api/stack') {
          return json(res, 200, MOCK_STACK);
        }

        if (url.pathname === '/api/state') {
          return json(res, 200, {
            ...MOCK_STACK,
            workingDiffKey: MOCK_WORKING_DIFF_KEY,
          });
        }

        if (url.pathname === '/api/review-state') {
          if (req.method === 'GET') {
            return json(res, 200, mockReviewState);
          }

          if (req.method === 'POST') {
            let body = '';
            req.on('data', (chunk: Buffer) => { body += chunk.toString(); });
            req.on('end', () => {
              try {
                const data = JSON.parse(body);
                if (data.diffStyle === 'unified' || data.diffStyle === 'split') {
                  mockReviewState = { ...mockReviewState, diffStyle: data.diffStyle };
                }
                if (typeof data.viewedScope === 'string' && typeof data.viewedFiles === 'object' && data.viewedFiles !== null) {
                  const nextViewedFiles = { ...mockReviewState.viewedFiles };
                  if (Object.keys(data.viewedFiles).length > 0) {
                    nextViewedFiles[data.viewedScope] = data.viewedFiles;
                  } else {
                    delete nextViewedFiles[data.viewedScope];
                  }
                  mockReviewState = {
                    ...mockReviewState,
                    viewedFiles: nextViewedFiles,
                  };
                }
                return json(res, 200, mockReviewState);
              } catch {
                return json(res, 400, { error: 'Invalid JSON body' });
              }
            });
            return;
          }
        }

        if (url.pathname === '/api/github-info') {
          return json(res, 200, {
            branches: {
              'feat/auth': { prNumber: 42, prUrl: 'https://github.com/example/repo/pull/42', prIsDraft: false, checkStatus: 'success' },
              'feat/login-form': { prNumber: 43, prUrl: 'https://github.com/example/repo/pull/43', prIsDraft: true, checkStatus: 'pending' },
              'feat/login-validation': { prNumber: null, prUrl: null, prIsDraft: false, checkStatus: null },
              'feat/oauth': { prNumber: 45, prUrl: 'https://github.com/example/repo/pull/45', prIsDraft: false, checkStatus: 'failure' },
              'feat/oauth-github': { prNumber: 46, prUrl: 'https://github.com/example/repo/pull/46', prIsDraft: false, checkStatus: 'success' },
              'feat/notifications': { prNumber: 50, prUrl: 'https://github.com/example/repo/pull/50', prIsDraft: false, checkStatus: 'pending' },
              'feat/email-alerts': { prNumber: 51, prUrl: 'https://github.com/example/repo/pull/51', prIsDraft: true, checkStatus: null },
            },
          });
        }

        if (url.pathname === '/api/diff/working') {
          return json(res, 200, { patch: MOCK_WORKING_PATCH });
        }

        if (url.pathname === '/api/diff') {
          const branch = url.searchParams.get('branch');
          if (!branch) {
            return json(res, 400, { error: 'Missing required query parameter: branch' });
          }
          const mock = MOCK_PATCHES[branch];
          if (!mock) {
            return json(res, 400, { error: `Branch '${branch}' is not tracked` });
          }
          return json(res, 200, {
            branch,
            parent: mock.parent,
            patch: normalizeMockPatch(mock.patch),
          });
        }

        if (req.method === 'POST' && url.pathname === '/api/move-lines') {
          let body = '';
          req.on('data', (chunk: Buffer) => { body += chunk.toString(); });
          req.on('end', () => {
            try {
              const data = JSON.parse(body);
              return json(res, 200, {
                sourceBranch: data.sourceBranch,
                targetBranch: data.targetBranch,
                filePath: data.filePath,
                restackedBranches: [],
              });
            } catch {
              return json(res, 400, { error: 'Invalid JSON body' });
            }
          });
          return;
        }

        if (url.pathname === '/api/review/models') {
          return json(res, 200, {
            models: [
              { id: 'claude:sonnet', name: 'Claude Sonnet 4.6', tool: 'claude', variant: 'sonnet', available: true },
              { id: 'claude:opus', name: 'Claude Opus 4.6', tool: 'claude', variant: 'opus', available: true },
              { id: 'claude:haiku', name: 'Claude Haiku', tool: 'claude', variant: 'haiku', available: true },
              { id: 'codex:gpt-5.4', name: 'Codex GPT-5.4', tool: 'codex', variant: 'gpt-5.4', available: true },
              { id: 'codex:gpt-5.4-mini', name: 'Codex GPT-5.4 Mini', tool: 'codex', variant: 'gpt-5.4-mini', available: true },
              { id: 'codex:gpt-5.3-codex', name: 'Codex GPT-5.3 Codex', tool: 'codex', variant: 'gpt-5.3-codex', available: true },
            ],
          });
        }

        if (req.method === 'POST' && url.pathname === '/api/review') {
          let body = '';
          req.on('data', (chunk: Buffer) => { body += chunk.toString(); });
          req.on('end', () => {
            res.writeHead(200, {
              'Content-Type': 'text/event-stream',
              'Cache-Control': 'no-cache',
              'Access-Control-Allow-Origin': '*',
            });

            const data = JSON.parse(body);
            const models: string[] = data.models ?? ['claude'];
            let delay = 800;

            for (const model of models) {
              setTimeout(() => {
                const event = JSON.stringify({
                  model,
                  summary: `${model === 'claude' ? 'Claude' : 'Codex'} review: The changes look reasonable overall. Consider adding input validation and improving error handling in a few places.`,
                  comments: [
                    {
                      file: 'src/auth.py',
                      start_line: 8,
                      end_line: 10,
                      side: 'additions',
                      text: 'Hard-coded credentials should be replaced with proper authentication.',
                      severity: 'error',
                    },
                    {
                      file: 'src/auth.py',
                      start_line: 14,
                      end_line: 14,
                      side: 'additions',
                      text: 'Consider adding a docstring explaining the expected token format.',
                      severity: 'suggestion',
                    },
                    {
                      file: 'src/middleware.py',
                      start_line: 7,
                      end_line: 12,
                      side: 'additions',
                      text: 'The Authorization header parsing could be more robust.',
                      severity: 'warning',
                    },
                  ],
                  error: null,
                });
                res.write(`event: review\ndata: ${event}\n\n`);
              }, delay);
              delay += 1200;
            }

            const synthesize: string | null = data.synthesize ?? null;
            if (synthesize && models.length >= 2) {
              setTimeout(() => {
                res.write('event: synthesis-start\ndata: {}\n\n');
              }, delay + 100);

              setTimeout(() => {
                const synthEvent = JSON.stringify({
                  model: synthesize,
                  summary: `Consolidated review: Both reviewers agree that hard-coded credentials are the highest-priority issue. The authentication header parsing concern was raised independently by both, confirming it needs attention. Input validation is a cross-cutting concern worth addressing.`,
                  comments: [
                    {
                      file: 'src/auth.py',
                      start_line: 8,
                      end_line: 10,
                      side: 'additions',
                      text: '[All reviewers] Hard-coded credentials must be replaced — this is a security vulnerability.',
                      severity: 'error',
                    },
                    {
                      file: 'src/middleware.py',
                      start_line: 7,
                      end_line: 12,
                      side: 'additions',
                      text: '[Consensus] Authorization header parsing needs proper error handling for malformed tokens.',
                      severity: 'warning',
                    },
                  ],
                  error: null,
                  fix_prompt: 'Fix the following issues:\n\n1. In src/auth.py at lines 8-10: Replace the hard-coded username/password check with a proper authentication backend (e.g. database lookup with hashed passwords). Remove the plaintext "admin"/"secret" credentials entirely.\n\n2. In src/middleware.py at lines 7-12: Add error handling for malformed Authorization headers — handle missing "Bearer " prefix, empty tokens, and invalid token formats gracefully instead of passing empty strings to get_current_user().',
                });
                res.write(`event: synthesis\ndata: ${synthEvent}\n\n`);
              }, delay + 1500);
              delay += 2000;
            }

            setTimeout(() => {
              res.write('event: done\ndata: {}\n\n');
              res.end();
            }, delay + 200);
          });
          return;
        }

        if (req.method === 'OPTIONS') {
          res.writeHead(200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Content-Length': '0',
          });
          res.end();
          return;
        }

        next();
      });
    },
  };
}
