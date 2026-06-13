# Cognito user pool backing the dev OIDC sign-in for the M9 product surface.
#
# The control plane verifies bearer ID tokens against this pool
# (AUTH_ISSUER_URL = the pool's cognito-idp issuer, AUTH_AUDIENCE = the app
# client id). The SPA uses the hosted-UI domain for the OAuth code+PKCE flow.
#
# These resources were first created out-of-band during the M9 dev rollout and
# are imported here. The pool's `schema` is intentionally ignored: Cognito
# provisions the full set of standard attributes and schema changes force a
# destructive pool replacement, so we manage only the email override on create
# and never diff it afterwards.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.42"
    }
  }
}

resource "aws_cognito_user_pool" "this" {
  name                     = var.pool_name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_uppercase                = true
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }

  tags = var.tags

  lifecycle {
    # Cognito ships ~20 standard attributes and treats schema as immutable;
    # any computed drift here would force-replace the pool. Manage existence
    # and policy, not the schema set.
    ignore_changes = [schema]
  }
}

# Public SPA client: no secret, OAuth code + PKCE for browser sign-in, plus
# ADMIN_USER_PASSWORD_AUTH so operators/CI can mint ID tokens for the smoke test.
resource "aws_cognito_user_pool_client" "ui" {
  name         = var.client_name
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  supported_identity_providers         = ["COGNITO"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["email", "openid", "profile"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls

  lifecycle {
    # generate_secret is create-only and isn't refreshed on import, so Terraform
    # would otherwise force-replace the client (changing its id and breaking
    # AUTH_AUDIENCE / OIDC_CLIENT_ID). It's already false (public SPA client).
    ignore_changes = [generate_secret]
  }
}

# Hosted-UI domain that serves /oauth2/authorize and /oauth2/token for the SPA.
resource "aws_cognito_user_pool_domain" "this" {
  domain       = var.hosted_ui_domain_prefix
  user_pool_id = aws_cognito_user_pool.this.id
}

# Cross-domain test users (tenant identity is derived from the email domain).
# Passwords are seeded out-of-band (admin-set-user-password) and not managed
# here — keeping credentials out of Terraform state.
resource "aws_cognito_user" "test" {
  for_each = var.test_user_emails

  user_pool_id = aws_cognito_user_pool.this.id
  username     = each.value

  attributes = {
    email          = each.value
    email_verified = "true"
  }

  lifecycle {
    ignore_changes = [password, temporary_password]
  }
}
