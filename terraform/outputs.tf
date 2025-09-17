output "app_name" {
  value = juju_application.temporal_ui_k8s.name
}

output "provides" {
  value = {
    ui = "ui"
  }
}

output "requires" {
  value = {
    ingress     = "ingress"
    nginx_route = "nginx-route"
  }
}
