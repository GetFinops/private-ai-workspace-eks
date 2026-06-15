"""Dev-only synthetic fixtures for the M13 integration harness.

These exist solely to exercise and dev-validate the shared harness against a
fake, in-cluster provider — never a real third-party service. Real integrations
are separate per-integration adoption decisions with their own credential
review; nothing here ships a real credential path.
"""
