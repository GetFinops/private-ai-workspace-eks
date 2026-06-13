variable "pool_name" {
  description = "Cognito user pool name."
  type        = string
}

variable "client_name" {
  description = "Name of the public SPA app client."
  type        = string
}

variable "hosted_ui_domain_prefix" {
  description = "Domain prefix for the Cognito hosted UI (e.g. private-ai-dev-xxxx)."
  type        = string
}

variable "callback_urls" {
  description = "Allowed OAuth callback URLs for the SPA."
  type        = list(string)
}

variable "logout_urls" {
  description = "Allowed sign-out redirect URLs for the SPA."
  type        = list(string)
}

variable "test_user_emails" {
  description = "Set of test user emails to seed (tenant = email domain). Passwords are set out-of-band."
  type        = set(string)
  default     = []
}

variable "tags" {
  description = "Additional tags to apply to the user pool."
  type        = map(string)
  default     = {}
}
