resource "google_service_account" "account" {
  for_each = var.accounts

  project         = var.project_id
  account_id      = each.key
  display_name    = each.value.display_name
  description     = each.value.description
  deletion_policy = "PREVENT"
}
