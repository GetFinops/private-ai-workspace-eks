variable "project_name" { type = string }
variable "environment" { type = string }
variable "cluster_name" { type = string }
variable "oidc_provider_arn" { type = string }
variable "oidc_provider_url" { type = string }
variable "service_account_namespace" {
  type    = string
  default = "inference"
}
variable "service_account_name" {
  type    = string
  default = "model-installer"
}
variable "tags" {
  type    = map(string)
  default = {}
}
