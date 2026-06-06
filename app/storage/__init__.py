"""Object-storage layer for the control plane.

Provides an S3-backed storage client (app.storage.s3) for uploads and
artifacts.  Requires boto3>=1.35 (Apache-2.0).

The client uses IRSA (IAM Roles for Service Accounts) in production —
no static credentials are used.  The IAM role ARN is configured via the
Kubernetes ServiceAccount annotation set by the Helm chart.
"""
