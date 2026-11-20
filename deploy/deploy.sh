#!/usr/bin/env bash
# Deploy Verity to Google Cloud: Artifact Registry -> Cloud Run (API) + Cloud Run Job (ingestion).
# Prereqs: gcloud auth login; MongoDB Atlas cluster on GCP in the same region; Vertex AI API enabled.
set -euo pipefail
PROJECT=${PROJECT:?set PROJECT}
REGION=${REGION:-us-central1}
REPO=${REPO:-verity}
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/verity:$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
SA="verity-run@$PROJECT.iam.gserviceaccount.com"

gcloud services enable run.googleapis.com aiplatform.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com --project "$PROJECT"

gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" --project "$PROJECT"

gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud iam service-accounts create verity-run --project "$PROJECT"
for role in roles/aiplatform.user roles/secretmanager.secretAccessor roles/cloudtrace.agent             roles/storage.objectViewer roles/run.invoker roles/eventarc.eventReceiver; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" --quiet >/dev/null
done

# Secrets (create once):  printf '%s' "$MONGODB_URI" | gcloud secrets create verity-mongodb-uri --data-file=-
#                         printf '%s' "$API_KEY"     | gcloud secrets create verity-api-key     --data-file=-
gcloud builds submit --tag "$IMAGE" --project "$PROJECT"

COMMON_ENV="GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$REGION,LLM_PROVIDER=gemini,EMBEDDING_PROVIDER=vertex,OTEL_EXPORTER=gcp"
SECRETS="MONGODB_URI=verity-mongodb-uri:latest,VERITY_API_KEY=verity-api-key:latest"

gcloud run deploy verity-api --image "$IMAGE" --region "$REGION" --project "$PROJECT" \
  --service-account "$SA" --set-env-vars "$COMMON_ENV" --set-secrets "$SECRETS" \
  --cpu 2 --memory 2Gi --concurrency 20 --min-instances 0 --max-instances 10 --timeout 300 \
  --cpu-boost --no-allow-unauthenticated

# Raw-source bucket + Eventarc: uploading gs://$BUCKET/<engagement_id>/<file> triggers POST /events/gcs on the API.
BUCKET=${BUCKET:-$PROJECT-verity-raw}
gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1 ||   gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access --project "$PROJECT"
GCS_SA="$(gcloud storage service-agent --project "$PROJECT")"
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$GCS_SA" --role roles/pubsub.publisher --quiet >/dev/null
gcloud eventarc triggers describe verity-gcs-ingest --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 ||   gcloud eventarc triggers create verity-gcs-ingest --location "$REGION" --project "$PROJECT"     --destination-run-service verity-api --destination-run-region "$REGION" --destination-run-path /events/gcs     --event-filters "type=google.cloud.storage.object.v1.finalized" --event-filters "bucket=$BUCKET"     --service-account "$SA"

# Bulk backfill job, e.g.: gcloud run jobs execute verity-ingest --args=-m,scripts.ingest,gs://$BUCKET/acme-payments,--engagement,acme-payments
gcloud run jobs deploy verity-ingest --image "$IMAGE" --region "$REGION" --project "$PROJECT"   --service-account "$SA" --set-env-vars "$COMMON_ENV" --set-secrets "$SECRETS"   --command python --args "-m,scripts.ingest,gs://$BUCKET/acme-payments,--engagement,acme-payments" --task-timeout 1800

echo "Deployed $IMAGE"
