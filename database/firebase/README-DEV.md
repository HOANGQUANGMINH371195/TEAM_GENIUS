# Firebase — developer guide

The browser uses only `NEXT_PUBLIC_FIREBASE_*` values. Backend Admin credentials
remain in the untracked environment/secret manager. Run the env contract check
before local startup and never commit service-account JSON or print it in logs.
