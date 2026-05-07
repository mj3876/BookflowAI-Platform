resource "google_storage_bucket_object" "vertex_pipeline_template" {
  bucket       = data.google_storage_bucket.models.name
  name         = var.vertex_pipeline_template_object
  source       = "${path.module}/pipelines/bookflow-existing-books-pipeline.json"
  content_type = "application/json"

  detect_md5hash = filemd5("${path.module}/pipelines/bookflow-existing-books-pipeline.json")
}
