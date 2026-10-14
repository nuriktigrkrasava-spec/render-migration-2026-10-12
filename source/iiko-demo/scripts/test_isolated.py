"""Legacy modules set DB_PATH before importing api; isolate each module."""
import os, subprocess, sys, tempfile, secrets
from pathlib import Path
root = Path(__file__).resolve().parents[1]
failed = []
files = sorted(root.glob("test_*.py")) + sorted((root / "tests").glob("test_*.py"))
for path in files:
    with tempfile.TemporaryDirectory(prefix="iiko-test-") as folder:
        env = dict(os.environ, DB_PATH=str(Path(folder)/"test.db"), ENVIRONMENT="test", WIDGET_SECRET=secrets.token_urlsafe(32))
        # No production secrets or paid API clients in automated tests.
        for key in ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "MIGRATE_TOKEN"]:
            env.pop(key, None)
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(path)], cwd=root, env=env)
        if result.returncode: failed.append(path.name)
print("Failed modules:", ", ".join(failed) if failed else "none")
sys.exit(bool(failed))
