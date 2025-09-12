variable "name" {
  type        = string
  description = "Name of the deployed application"
  default     = "temporal-ui-k8s"
}

variable "units" {
  type        = number
  description = "Number of units to deploy with this name and configuration"
  default     = 1
}

variable "model" {
  type        = string
  description = "Juju model where the application is to be deployed"
}

variable "revision" {
  type        = number
  description = "Revision of the charm to deploy"
  default     = 19
}

variable "channel" {
  type        = string
  description = "Charmhub channel to deploy the charm from"
  default     = "1.23/edge" # TODO: change to 1.23/stable
}

variable "log_level" {
  type        = string
  description = "The log level of gunicorn"
  default     = "info"
}

variable "external_hostname" {
  type        = string
  description = "The DNS listing used for external connections"
  default     = "temporal-ui-k8s"
}

variable "tls_secret_name" {
  type        = string
  description = "Name of the k8s secret which contains the TLS certificate to be used by ingress"
  default     = "temporal-tls"
}

variable "port" {
  type        = number
  description = "The port used by the Temporal Web UI Server and the HTTP API"
  default     = 8080
}

variable "default_namespace" {
  type        = string
  description = "The default Temporal namespace that the Web UI opens first"
  default     = "default"
}

variable "auth" {
  type = object({
    enabled       = optional(bool, false),
    provider_url  = optional(string, "https://accounts.google.com"),
    client_id     = optional(string, ""),
    client_secret = optional(string, ""),
    scopes        = optional(string, "[openid,profile,email]")
  })
  description = "Auth related configurations"
  default     = {}
}

variable "codec" {
  type = object({
    endpoint          = optional(string, ""),
    pass_access_token = optional(bool, false)
  })
  description = "Codec server related configurations"
  default     = {}
}

variable "workflow" {
  type = object({
    terminate_disabled = optional(bool, false),
    cancel_disabled    = optional(bool, false),
    signal_disabled    = optional(bool, false),
    reset_disabled     = optional(bool, false)
  })
  description = "Workflow related configurations"
  default     = {}
}

variable "batch_actions_disabled" {
  type        = bool
  description = "Whether or not batch actions are disabled through the UI"
  default     = false
}

variable "hide_workflow_query_errors" {
  type        = bool
  description = "Whether or not workflow query errors are hidden on the UI"
  default     = false
}
