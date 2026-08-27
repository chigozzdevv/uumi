resource "google_project_service_identity" "storage" {
  provider = google-beta

  project = var.project_id
  service = "storage.googleapis.com"
}

resource "google_project_service_identity" "aiplatform" {
  provider = google-beta

  project = var.project_id
  service = "aiplatform.googleapis.com"
}

resource "google_kms_key_ring" "agents" {
  project  = var.project_id
  name     = "uumi-agents"
  location = var.location
}

resource "google_kms_crypto_key" "agents" {
  name            = "runtime"
  key_ring        = google_kms_key_ring.agents.id
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  crypto_members = {
    storage  = "serviceAccount:service-${var.project_number}@gs-project-accounts.iam.gserviceaccount.com"
    platform = google_project_service_identity.aiplatform.member
    runtime  = "serviceAccount:service-${var.project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
    deployer = var.deployment_member
  }
}

resource "google_kms_crypto_key_iam_member" "agents" {
  for_each = local.crypto_members

  crypto_key_id = google_kms_crypto_key.agents.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}

resource "google_storage_bucket" "agents" {
  project                     = var.project_id
  name                        = "${var.project_id}-uumi-agents"
  location                    = var.location
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  encryption {
    default_kms_key_name = google_kms_crypto_key.agents.id
  }

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.agents]

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "objects" {
  bucket = google_storage_bucket.agents.name
  role   = "roles/storage.objectUser"
  member = var.deployment_member
}

resource "google_storage_bucket_iam_member" "bucket" {
  bucket = google_storage_bucket.agents.name
  role   = "roles/storage.legacyBucketReader"
  member = var.deployment_member
}
