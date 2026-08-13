resource "google_compute_network" "firekey" {
  project                 = var.project_id
  name                    = "firekey"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "browser" {
  project                  = var.project_id
  region                   = var.region
  name                     = "firekey-browser"
  network                  = google_compute_network.firekey.id
  ip_cidr_range            = "10.76.0.0/24"
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 1
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_router" "browser" {
  project = var.project_id
  region  = var.region
  name    = "firekey-browser"
  network = google_compute_network.firekey.id
}

resource "google_compute_router_nat" "browser" {
  project                            = var.project_id
  region                             = var.region
  name                               = "firekey-browser"
  router                             = google_compute_router.browser.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.browser.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ALL"
  }
}

resource "google_compute_firewall" "worker" {
  project   = var.project_id
  name      = "firekey-browser-worker"
  network   = google_compute_network.firekey.name
  direction = "INGRESS"
  priority  = 1000

  source_ranges = [google_compute_subnetwork.browser.ip_cidr_range]
  target_tags   = ["firekey-browser"]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "egress" {
  project   = var.project_id
  name      = "firekey-browser-egress"
  network   = google_compute_network.firekey.name
  direction = "EGRESS"
  priority  = 1000

  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["firekey-browser"]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  allow {
    protocol = "udp"
    ports    = ["53"]
  }

  allow {
    protocol = "tcp"
    ports    = ["53"]
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_instance_template" "browser" {
  project      = var.project_id
  name_prefix  = "firekey-browser-"
  description  = "Single-run shielded FireKey Computer Use worker."
  machine_type = "e2-standard-4"
  region       = var.region
  tags         = ["firekey-browser"]

  disk {
    source_image = "projects/cos-cloud/global/images/family/cos-stable"
    auto_delete  = true
    boot         = true
    disk_type    = "pd-balanced"
    disk_size_gb = 30
    disk_encryption_key {
      kms_key_self_link = google_kms_crypto_key.browser.id
    }
  }

  network_interface {
    network    = google_compute_network.firekey.id
    subnetwork = google_compute_subnetwork.browser.id
  }

  service_account {
    email  = var.worker_service_account
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    automatic_restart   = false
    on_host_maintenance = "TERMINATE"
    preemptible         = false

    max_run_duration {
      seconds = 7200
    }

    instance_termination_action = "DELETE"
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
    startup-script         = file("${path.module}/startup.sh")
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_kms_key_ring" "browser" {
  project  = var.project_id
  name     = "firekey-browser"
  location = var.region
}

resource "google_kms_crypto_key" "browser" {
  name            = "browser-disk"
  key_ring        = google_kms_key_ring.browser.id
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_service_identity" "compute" {
  provider = google-beta
  project  = var.project_id
  service  = "compute.googleapis.com"
}

resource "google_kms_crypto_key_iam_member" "compute" {
  crypto_key_id = google_kms_crypto_key.browser.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.compute.member
}

resource "google_project_iam_member" "coordinator_compute" {
  project = var.project_id
  role    = "roles/compute.instanceAdmin.v1"
  member  = var.coordinator_member
}

resource "google_service_account_iam_member" "coordinator_worker" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.worker_service_account}"
  role               = "roles/iam.serviceAccountUser"
  member             = var.coordinator_member
}
