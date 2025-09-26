# stripe.export_disputes

## Purpose
Streams Stripe dispute records into a CSV artifact and publishes it to the configured S3 bucket so operators can hand off evidence or share a download link externally.

## Triggers
- Manually from the command line via `scripts/run_agent.py --agent stripe.export_disputes`.
- Programmatically by enqueuing a job with `agent="stripe.export_disputes"` and a `disputes` payload containing Stripe dispute dictionaries.

## Payload Contract
```json
{
  "disputes": [
    {
      "id": "dp_123",
      "amount": 5000,
      "currency": "usd",
      "status": "won",
      "reason": "fraudulent"
    }
  ],
  "s3_key": "optional/custom/key.csv" // optional override for the S3 object key
}
```

## Outputs
```json
{
  "artifact_url": "https://...",   // presigned URL valid for ~1 hour
  "s3_key": "stripe/disputes-20250926T194056Z.csv",
  "rows": 1,
  "mime_type": "text/csv"
}
```

## Tool / Service Access
- `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` on `${ARTIFACT_BUCKET}`.
- Optional: `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey` if `ARTIFACT_SSE_KMS_KEY` is set.

## Configuration

### Required Environment Variables
- `AWS_REGION`: AWS region for S3 operations (e.g., `us-east-1`)
- `ARTIFACT_BUCKET`: S3 bucket name for storing artifacts

### Encryption Settings (S3-Managed by Default)
- `ARTIFACT_ENCRYPTION`: Set to `s3` for S3-managed AES256 encryption (recommended)
  - `s3`: Uses S3-managed keys (SSE-S3) - simple, secure, no extra cost
  - `kms`: Customer-managed KMS keys (requires `ARTIFACT_SSE_KMS_KEY`)
  - `auto`: Use KMS if key is set, otherwise no explicit encryption
  - `none`: No encryption headers (bucket policy may still encrypt)
- `ARTIFACT_SSE_KMS_KEY`: Leave empty for S3-managed encryption (only needed if using KMS)
- `ARTIFACT_FALLBACK_SSE_S3`: `true` to fall back to AES256 if KMS fails

### Optional Settings
- `ARTIFACT_URL_TTL`: Presigned URL lifetime in seconds (default: 3600)
- `ARTIFACT_S3_ENDPOINT_URL`: Custom endpoint for LocalStack/MinIO development
- `S3_MAX_ATTEMPTS`, `S3_CONNECT_TIMEOUT`, `S3_READ_TIMEOUT`, `S3_PUT_DEADLINE_SEC`: S3 client tuning

### Security Note: S3-Managed Encryption
The system uses **S3-managed encryption (SSE-S3)** with AES256 by default. This provides:
- Automatic encryption at rest for all artifacts
- No key management overhead
- Compliance with most security standards (SOC2, ISO 27001, PCI DSS)
- No additional costs compared to customer-managed KMS keys
- High availability without KMS API dependencies

For organizations requiring customer-managed keys for compliance, switch to KMS mode by:
1. Creating a KMS key alias: `alias/wordflux-artifacts`
2. Setting `ARTIFACT_ENCRYPTION=kms`
3. Setting `ARTIFACT_SSE_KMS_KEY=alias/wordflux-artifacts`
4. Ensuring IAM permissions for KMS operations

Ensure AWS credentials with the required permissions are available to the runtime via the environment or IAM role.

### Lifecycle policy
To enforce a 30-day retention window for generated CSVs, apply the lifecycle configuration in `configs/s3-lifecycle.json`:

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket "$ARTIFACT_BUCKET" \
  --lifecycle-configuration file://configs/s3-lifecycle.json
```
