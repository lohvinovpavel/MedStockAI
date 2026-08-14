# The base manifests use nginx.ingress.kubernetes.io/rewrite-target, which
# GKE's built-in GCE ingress controller does not understand — see
# docs/infra-dev-plan.md §0.1. ingress-nginx is required, not a preference.
resource "helm_release" "ingress_nginx" {
  name             = "ingress-nginx"
  repository       = "https://kubernetes.github.io/ingress-nginx"
  chart            = "ingress-nginx"
  version          = "4.11.3"
  namespace        = "ingress-nginx"
  create_namespace = true

  set {
    name  = "controller.service.loadBalancerIP"
    value = google_compute_address.ingress.address
  }

  depends_on = [google_container_node_pool.spot]
}

# installCRDs=true: the ClusterIssuer that uses this CRD is intentionally
# NOT a Terraform resource (see docs/infra-dev-plan.md §2) — it ships as
# YAML in the dev overlay, applied after this Helm release exists.
resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = "v1.16.2"
  namespace        = "cert-manager"
  create_namespace = true

  set {
    name  = "installCRDs"
    value = "true"
  }

  depends_on = [google_container_node_pool.spot]
}
