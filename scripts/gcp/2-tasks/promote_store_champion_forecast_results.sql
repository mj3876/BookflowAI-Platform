DELETE FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_results`
WHERE prediction_date = DATE '2026-05-19';

INSERT INTO `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_results` (
  prediction_date,
  target_date,
  isbn13,
  store_id,
  predicted_demand,
  confidence_low,
  confidence_high,
  model_version,
  inference_ms
)
SELECT
  prediction_date,
  target_date,
  isbn13,
  store_id,
  CAST(predicted_demand AS NUMERIC) AS predicted_demand,
  CAST(confidence_low AS NUMERIC) AS confidence_low,
  CAST(confidence_high AS NUMERIC) AS confidence_high,
  model_version,
  inference_ms
FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw._store_champion_predictions_real_aladin_20260519`;
