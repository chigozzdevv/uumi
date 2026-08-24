resource "google_identity_platform_config" "default" {
  project = var.project_id

  sign_in {
    email {
      enabled           = false
      password_required = true
    }
  }

  authorized_domains = var.authorized_domains
}
