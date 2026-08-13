output "name" {
  description = "Rotation workflow resource name, or null before runtime deployment."
  value       = try(google_workflows_workflow.rotation["rotation"].id, null)
}
