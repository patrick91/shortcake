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
      name: 'feat/oauth',
      parent: 'feat/auth',
      depth: 1,
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
