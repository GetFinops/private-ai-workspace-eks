"""Database layer for the control plane.

Provides a connection pool (app.db.connection) and a SQL migration runner
(app.db.migrations). Both modules require psycopg[binary]>=3.1 (LGPL-2.1).
"""
