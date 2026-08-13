resource "google_storage_bucket" "state" {
  name                        = var.bucket_name
  project                     = var.project_id
  location                    = var.region
  force_destroy               = false
  public_access_prevention    = "enforced"
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age        = 90
      with_state = "ARCHIVED"
    }

    action {
      type = "Delete"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

