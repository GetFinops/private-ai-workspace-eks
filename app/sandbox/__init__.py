"""Sandbox package — dependency-light by design.

Nothing here may import the control plane, config, database, or AWS SDK: the
out-of-process tool runner (app/sandbox/runner.py) is launched as
``python3 -m app.sandbox.runner`` with a scrubbed environment, and this package
initialiser must stay free of heavy/credentialed imports for that isolation to
hold.
"""
