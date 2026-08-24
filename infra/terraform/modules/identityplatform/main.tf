resource "google_identity_platform_config" "default" {
  project = var.project_id

  sign_in {
    email {
      enabled           = true
      password_required = true
    }

    phone_number {
      enabled = false
    }
  }

  authorized_domains = var.authorized_domains
}
