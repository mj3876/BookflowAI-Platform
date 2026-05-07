import logging
import os

import functions_framework
from google.cloud import aiplatform


def _json_error(message, status_code):
    return ({"error": message}, status_code)


@functions_framework.http
def handler(request):
    try:
        payload = request.get_json(silent=True) or {}
        project_id = payload.get("project_id") or os.getenv("BOOKFLOW_PROJECT_ID")
        location = payload.get("location") or payload.get("endpoint_location") or os.getenv(
            "BOOKFLOW_VERTEX_LOCATION"
        )
        endpoint_id = payload.get("endpoint_id") or payload.get("endpoint") or os.getenv(
            "BOOKFLOW_VERTEX_ENDPOINT"
        )
        instances = payload.get("instances") or payload.get("features")

        if isinstance(instances, dict) and "instances" in instances:
            instances = instances["instances"]
        if isinstance(instances, dict):
            instances = [instances]

        missing = [
            name
            for name, value in {
                "project_id": project_id,
                "location": location,
                "endpoint_id": endpoint_id,
                "instances": instances,
            }.items()
            if not value
        ]
        if missing:
            return _json_error(f"Missing required fields: {', '.join(missing)}", 400)

        aiplatform.init(project=project_id, location=location)
        endpoint = aiplatform.Endpoint(endpoint_name=endpoint_id)
        prediction = endpoint.predict(instances=instances)

        return {
            "predictions": prediction.predictions,
            "deployed_model_id": prediction.deployed_model_id,
            "model_version_id": prediction.model_version_id,
            "metadata": prediction.metadata,
        }
    except Exception as exc:
        logging.exception("Vertex AI prediction failed")
        return _json_error(str(exc), 500)
