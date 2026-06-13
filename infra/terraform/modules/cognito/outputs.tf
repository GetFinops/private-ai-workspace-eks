output "user_pool_id" {
  description = "Cognito user pool id."
  value       = aws_cognito_user_pool.this.id
}

output "issuer_url" {
  description = "OIDC issuer URL — set as the control plane's AUTH_ISSUER_URL and the UI's OIDC_ISSUER."
  value       = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
}

output "client_id" {
  description = "App client id — set as the control plane's AUTH_AUDIENCE and the UI's OIDC_CLIENT_ID."
  value       = aws_cognito_user_pool_client.ui.id
}

output "hosted_ui_domain" {
  description = "Fully-qualified hosted-UI domain serving /oauth2/authorize and /oauth2/token."
  value       = "${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.region}.amazoncognito.com"
}

data "aws_region" "current" {}
