resource "juju_application" "temporal_ui_k8s" {
  name  = var.name
  model = var.model

  charm {
    name     = "temporal-ui-k8s"
    revision = var.revision
    channel  = var.channel
  }

  config = {
    log-level = var.log_level

    external-hostname = var.external_hostname
    tls-secret-name   = var.tls_secret_name
    port              = var.port

    default-namespace = var.default_namespace

    auth-enabled       = var.auth["enabled"]
    auth-provider-url  = var.auth["provider_url"]
    auth-client-id     = var.auth["client_id"]
    auth-client-secret = var.auth["client_secret"]
    auth-scopes        = var.auth["scopes"]

    codec-endpoint          = var.codec["endpoint"]
    codec-pass-access-token = var.codec["pass_access_token"]

    workflow-terminate-disabled = var.workflow["terminate_disabled"]
    workflow-cancel-disabled    = var.workflow["cancel_disabled"]
    workflow-signal-disabled    = var.workflow["signal_disabled"]
    workflow-reset-disabled     = var.workflow["reset_disabled"]

    batch-actions-disabled = var.batch_actions_disabled

    hide-workflow-query-errors = var.hide_workflow_query_errors
  }

  units = var.units
}
