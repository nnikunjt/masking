# Deployment Procedure

## Services Involved

| Service | Role | Identifier |
|---|---|---|
| GitHub Pages | Hosts the static frontend (`index.html`) | `https://nnikunjt.github.io` (repo `nnikunjt/masking`) |
| Amazon API Gateway | HTTPS entry point the frontend calls | Prod: `https://iqdfxko6jk.execute-api.us-east-1.amazonaws.com/default/VDB-masking-feature` (us-east-1)<br>Dev: `https://wp59s0wcwd.execute-api.ap-south-1.amazonaws.com/default/VDB-Masking-Dev` (ap-south-1) |
| AWS Lambda | Runs the masking logic (container image, Python 3.11 base) | Prod: `VDB-masking-feature` (us-east-1)<br>Dev: `VDB-Masking-Dev` (ap-south-1)<br>Handler: `lambda_function_pymupdf.lambda_handler` |
| Amazon ECR | Stores the Lambda container image | us-east-1: `025066257788.dkr.ecr.us-east-1.amazonaws.com/vdb/masking`<br>ap-south-1: `025066257788.dkr.ecr.ap-south-1.amazonaws.com/vdb/masking` (separate repo per region — no replication configured) |
| Amazon S3 | Stores masked output files and temp uploads used for Textract | `masked-certificates-bucket` |
| Amazon Textract | OCR for image inputs (`.jpg`/`.png`) | called directly from `lambda_function_pymupdf.py` |
| Docker | Builds the Lambda container image locally | `linux/amd64` platform (required even when building on Apple Silicon, since Lambda runs on x86_64) |

Account: `025066257788`.

## Prerequisites

- Docker with `linux/amd64` build support (buildx/emulation on Apple Silicon)
- AWS CLI v2 configured with credentials for account `025066257788` with `ecr:*` and `lambda:UpdateFunctionCode` permissions
- Repo cloned locally
- `gitleaks` installed (`brew install gitleaks`) and hooks enabled once per clone:

  ```bash
  git config core.hooksPath .githooks
  ```

  This blocks commits containing AWS keys/tokens before they reach git history. AWS credentials belong in `lambda/.env` (gitignored) or the Lambda execution role/environment — never hardcoded in source.

## A. Backend (Lambda) deployment

1. Make code changes in `lambda/lambda_function_pymupdf.py` (the active handler per the Dockerfile's `CMD`) or `lambda/requirements.txt`.
2. Build, tag, and push the image via the `lambda/Makefile`:

   ```bash
   cd lambda

   # Prod (us-east-1) — default region in the Makefile
   make deploy

   # Dev (ap-south-1) — override the region
   make deploy AWS_REGION=ap-south-1
   ```

   `make deploy` runs `login → build → tag → push` and publishes to the `:latest` tag in the region-specific ECR repo.

3. **Point Lambda at the new image.** Pushing `:latest` to ECR does *not* redeploy the function — Lambda is pinned to the image digest it last resolved. This step is not currently in the Makefile and must be run manually:

   ```bash
   aws lambda update-function-code \
     --region us-east-1 \
     --function-name VDB-masking-feature \
     --image-uri 025066257788.dkr.ecr.us-east-1.amazonaws.com/vdb/masking:latest

   aws lambda update-function-code \
     --region ap-south-1 \
     --function-name VDB-Masking-Dev \
     --image-uri 025066257788.dkr.ecr.ap-south-1.amazonaws.com/vdb/masking:latest
   ```

4. Wait for the update to finish before testing:

   ```bash
   aws lambda wait function-updated --region us-east-1 --function-name VDB-masking-feature
   ```

5. Smoke test against the live endpoint (see `Masking-curl.yaml` for a ready-made request) and confirm it returns a valid S3 link to the masked output.

