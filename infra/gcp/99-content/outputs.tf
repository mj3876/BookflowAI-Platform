output "cloud_function_uris" {
  description = "Internal Cloud Function URIs."
  value = {
    for key, function in google_cloudfunctions2_function.content :
    key => function.service_config[0].uri
  }
}

output "workflow_id" {
  description = "Content routing workflow id."
  value       = google_workflows_workflow.gcs_router.id
}

output "eventarc_trigger_id" {
  description = "GCS finalize Eventarc trigger id."
  value       = google_eventarc_trigger.gcs_finalize.id
}

output "vertex_endpoint_name" {
  description = "Vertex AI private endpoint resource name."
  value       = google_vertex_ai_endpoint.forecast.name
}
