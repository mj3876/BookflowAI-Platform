"""
학습 완료된 XGBoost 모델을 endpoint에 배포하고 backup-494808에 복사
Model: projects/476598540719/locations/asia-northeast1/models/6477445100577226752
"""
from google.cloud import aiplatform, storage

PROJECT  = "project-8ab6bf05-54d2-4f5d-b8d"
BACKUP   = "backup-494808"
LOCATION = "asia-northeast1"
BUCKET   = f"{PROJECT}-bookflow-models"
MODEL_GCS_PREFIX = "vertex-models/bookflow-xgb-demand-v1"
MODEL_RN  = "projects/476598540719/locations/asia-northeast1/models/6477445100577226752"
ENDPOINT_RN = "projects/476598540719/locations/asia-northeast1/endpoints/bookflow-forecast-endpoint"

aiplatform.init(project=PROJECT, location=LOCATION)

# ── 1. Endpoint 배포 ───────────────────────────────────────────────────────────
print("[1/3] Endpoint 배포 ...")
model    = aiplatform.Model(MODEL_RN)
endpoint = aiplatform.Endpoint(ENDPOINT_RN)
print(f"  model:    {model.display_name}")
print(f"  endpoint: {endpoint.display_name}")

endpoint.deploy(
    model=model,
    deployed_model_display_name="bookflow-xgb-v1",
    machine_type="n1-standard-2",
    min_replica_count=1,
    max_replica_count=1,
    traffic_percentage=100,
    sync=True,
)
print("  배포 완료")

# ── 2. GCS 모델 파일을 backup-494808 버킷으로 복사 ─────────────────────────────
print("\n[2/3] GCS 모델 backup-494808 복사 ...")
src_gcs    = storage.Client(project=PROJECT)
backup_gcs = storage.Client(project=BACKUP)

backup_bucket_name = "backup-494808-bookflow-models"
try:
    backup_bucket = backup_gcs.get_bucket(backup_bucket_name)
    print(f"  기존 버킷 사용: gs://{backup_bucket_name}")
except Exception:
    backup_bucket = backup_gcs.create_bucket(backup_bucket_name, location=LOCATION)
    print(f"  버킷 생성: gs://{backup_bucket_name}")

src_blob  = src_gcs.bucket(BUCKET).blob(f"{MODEL_GCS_PREFIX}/model.bst")
dst_blob  = backup_bucket.blob(f"{MODEL_GCS_PREFIX}/model.bst")
token = None
token, _, _ = dst_blob.rewrite(src_blob)
while token:
    token, _, _ = dst_blob.rewrite(src_blob, rewrite_token=token)
print(f"  gs://{backup_bucket_name}/{MODEL_GCS_PREFIX}/model.bst 복사 완료")

# ── 3. backup-494808 Model Registry 등록 ──────────────────────────────────────
print("\n[3/3] backup-494808 Model Registry 등록 ...")
aiplatform.init(project=BACKUP, location=LOCATION)
backup_model = aiplatform.Model.upload(
    display_name="bookflow-xgb-demand-forecast",
    artifact_uri=f"gs://{backup_bucket_name}/{MODEL_GCS_PREFIX}",
    serving_container_image_uri=(
        "us-docker.pkg.dev/vertex-ai/prediction/xgboost-cpu.1-7:latest"
    ),
    description="BookFlow demand forecast XGBoost v1 - backup copy from project-8ab6bf05",
    labels={"project": "bookflow", "environment": "backup"},
)
print(f"  등록 완료: {backup_model.resource_name}")

print("\n완료!")
print(f"  원본 Model : {MODEL_RN}")
print(f"  backup GCS : gs://{backup_bucket_name}/{MODEL_GCS_PREFIX}/model.bst")
print(f"  backup Model: {backup_model.resource_name}")
