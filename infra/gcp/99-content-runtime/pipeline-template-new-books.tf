resource "google_storage_bucket_object" "new_books_pipeline_template" {
  bucket       = data.google_storage_bucket.models.name
  name         = var.new_book_pipeline_template_object
  source       = "${path.module}/pipelines/bookflow-new-books-pipeline.json"
  content_type = "application/json"

  detect_md5hash = filemd5("${path.module}/pipelines/bookflow-new-books-pipeline.json")
}
