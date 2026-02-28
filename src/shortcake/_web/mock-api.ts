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
  ],
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

        if (url.pathname === '/api/diff') {
          const branch = url.searchParams.get('branch');
          if (!branch) {
            return json(res, 400, { error: 'Missing required query parameter: branch' });
          }
          const mock = MOCK_PATCHES[branch];
          if (!mock) {
            return json(res, 400, { error: `Branch '${branch}' is not tracked` });
          }
          return json(res, 200, { branch, parent: mock.parent, patch: mock.patch });
        }

        next();
      });
    },
  };
}
