#Requires -Version 5.1
<#
.SYNOPSIS
    Deploy Skylize to AWS staging.
.DESCRIPTION
    Runs: Terraform init/plan/apply → ECR push → DB migrations → smoke test.
    Run from the repo root on Windows 11 with AWS CLI v2, Docker Desktop, Terraform installed.
.PARAMETER SkipTerraform
    Skip Terraform apply (useful if infra already exists).
.PARAMETER SkipMigrations
    Skip running alembic migrations via ECS one-off task.
.PARAMETER PlanOnly
    Run terraform plan but do not apply.
.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -SkipTerraform
    .\deploy.ps1 -PlanOnly
#>
param(
    [switch]$SkipTerraform,
    [switch]$SkipMigrations,
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$AWS_REGION      = "us-east-1"
$TF_DIR          = "infra\terraform\staging"
$ECR_REPO_NAME   = "skylize-api"
$ECS_CLUSTER     = "skylize-staging"
$ECS_SERVICE     = "skylize-staging-api"
$ECS_TASK_FAMILY = "skylize-staging-api"
$CONTAINER_NAME  = "api"
$HEALTH_PATH     = "/health"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Step { param([string]$Msg) Write-Host "`n==> $Msg" -ForegroundColor Cyan }
function Ok   { param([string]$Msg) Write-Host "    OK: $Msg" -ForegroundColor Green }
function Fail { param([string]$Msg) Write-Host "FAIL: $Msg" -ForegroundColor Red; exit 1 }

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "$Name not found in PATH. Install it and retry."
    }
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────
Step "Pre-flight checks"
Assert-Command aws
Assert-Command terraform
Assert-Command docker

$Identity = aws sts get-caller-identity --output json 2>&1
if ($LASTEXITCODE -ne 0) { Fail "AWS CLI not authenticated. Run: aws sso login OR set AWS_PROFILE" }
$AccountId = ($Identity | ConvertFrom-Json).Account
Ok "AWS account: $AccountId"

# ── Bootstrap Terraform state backend (idempotent) ────────────────────────────
Step "Bootstrap Terraform S3 state backend"
$StateBucket = "skylize-terraform-state-staging"
$LockTable   = "skylize-terraform-locks"

$BucketExists = aws s3api head-bucket --bucket $StateBucket 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Creating S3 bucket $StateBucket..."
    aws s3api create-bucket --bucket $StateBucket --region $AWS_REGION
    aws s3api put-bucket-versioning --bucket $StateBucket `
        --versioning-configuration Status=Enabled
    aws s3api put-bucket-encryption --bucket $StateBucket `
        --server-side-encryption-configuration '{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"AES256\"}}]}'
    aws s3api put-public-access-block --bucket $StateBucket `
        --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
    Ok "S3 bucket created"
} else {
    Ok "S3 bucket already exists"
}

$TableExists = aws dynamodb describe-table --table-name $LockTable --region $AWS_REGION 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Creating DynamoDB lock table $LockTable..."
    aws dynamodb create-table `
        --table-name $LockTable `
        --attribute-definitions AttributeName=LockID,AttributeType=S `
        --key-schema AttributeName=LockID,KeyType=HASH `
        --billing-mode PAY_PER_REQUEST `
        --region $AWS_REGION
    Ok "DynamoDB table created"
} else {
    Ok "DynamoDB table already exists"
}

# ── Terraform ─────────────────────────────────────────────────────────────────
if (-not $SkipTerraform) {
    Push-Location $TF_DIR

    Step "Terraform init"
    terraform init -upgrade
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "terraform init failed" }

    Step "Terraform plan"
    terraform plan -out=tfplan
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "terraform plan failed" }

    if ($PlanOnly) {
        Ok "Plan only — skipping apply."
        Pop-Location
    } else {
        Step "Terraform apply"
        terraform apply -auto-approve tfplan
        if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "terraform apply failed" }
        Ok "Infrastructure deployed"

        # Capture outputs
        $TF_OUTPUTS = terraform output -json | ConvertFrom-Json
        $ECR_URL    = $TF_OUTPUTS.ecr_repository_url.value
        $ALB_DNS    = $TF_OUTPUTS.alb_dns_name.value
        Pop-Location

        # ── Docker build + ECR push ────────────────────────────────────────────
        Step "ECR login"
        aws ecr get-login-password --region $AWS_REGION |
            docker login --username AWS --password-stdin "$AccountId.dkr.ecr.$AWS_REGION.amazonaws.com"
        if ($LASTEXITCODE -ne 0) { Fail "ECR login failed" }

        $IMAGE_TAG = (git rev-parse --short HEAD 2>$null)
        if (-not $IMAGE_TAG) { $IMAGE_TAG = "initial" }
        $FULL_TAG  = "${ECR_URL}:${IMAGE_TAG}"
        $LATEST    = "${ECR_URL}:latest"

        Step "Docker build"
        docker build -t $FULL_TAG -t $LATEST -f Dockerfile .
        if ($LASTEXITCODE -ne 0) { Fail "Docker build failed" }

        Step "Docker push to ECR"
        docker push $FULL_TAG
        docker push $LATEST
        if ($LASTEXITCODE -ne 0) { Fail "Docker push failed" }
        Ok "Image pushed: $FULL_TAG"

        # ── Populate Secrets Manager (prompt if not set) ───────────────────────
        Step "Secrets Manager — populate (edit values first if prompted)"
        Write-Host @"
  REMINDER: Populate these secrets before migrations will succeed.
  Use AWS Console or:

    aws secretsmanager put-secret-value \
      --secret-id /skylize/staging/DATABASE_URL \
      --secret-string 'postgresql://skylize:<password>@<rds-endpoint>:5432/skylize'

    aws secretsmanager put-secret-value \
      --secret-id /skylize/staging/DATABASE_APP_URL \
      --secret-string 'postgresql://skylize_app:<password>@<rds-endpoint>:5432/skylize'

    aws secretsmanager put-secret-value \
      --secret-id /skylize/staging/REDIS_URL \
      --secret-string 'redis://<elasticache-endpoint>:6379'

    aws secretsmanager put-secret-value \
      --secret-id /skylize/staging/GOVERNANCE_SIGNING_KEY_PEM \
      --secret-string "$(python scripts/gen_governance_key.py 2>/dev/null || echo '<run python scripts/gen_governance_key.py>')"

"@ -ForegroundColor Yellow

        # ── Run DB migrations via ECS one-off task ─────────────────────────────
        if (-not $SkipMigrations) {
            Step "Running DB migrations via ECS one-off task"

            $TASK_DEF_ARN = aws ecs describe-task-definition `
                --task-definition $ECS_TASK_FAMILY `
                --query "taskDefinition.taskDefinitionArn" `
                --output text

            # Get subnet + SG from the service
            $SVC = aws ecs describe-services `
                --cluster $ECS_CLUSTER `
                --services $ECS_SERVICE `
                --query "services[0].networkConfiguration.awsvpcConfiguration" `
                --output json | ConvertFrom-Json

            $SUBNET = $SVC.subnets[0]
            $SG     = $SVC.securityGroups[0]

            $TASK_ARN = aws ecs run-task `
                --cluster $ECS_CLUSTER `
                --task-definition $TASK_DEF_ARN `
                --launch-type FARGATE `
                --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=DISABLED}" `
                --overrides "{`"containerOverrides`":[{`"name`":`"$CONTAINER_NAME`",`"command`":[`"alembic`",`"upgrade`",`"head`"]}]}" `
                --query "tasks[0].taskArn" `
                --output text

            Write-Host "    Migration task: $TASK_ARN"
            Write-Host "    Waiting for completion..."

            aws ecs wait tasks-stopped `
                --cluster $ECS_CLUSTER `
                --tasks $TASK_ARN

            $EXIT_CODE = aws ecs describe-tasks `
                --cluster $ECS_CLUSTER `
                --tasks $TASK_ARN `
                --query "tasks[0].containers[0].exitCode" `
                --output text

            if ($EXIT_CODE -ne "0") { Fail "Migration task exited with code $EXIT_CODE" }
            Ok "Migrations complete"
        }

        # ── Force ECS service update ───────────────────────────────────────────
        Step "Triggering ECS rolling deploy"
        aws ecs update-service `
            --cluster $ECS_CLUSTER `
            --service $ECS_SERVICE `
            --force-new-deployment `
            --region $AWS_REGION | Out-Null

        Write-Host "    Waiting for service stability (up to 5 min)..."
        aws ecs wait services-stable `
            --cluster $ECS_CLUSTER `
            --services $ECS_SERVICE `
            --region $AWS_REGION

        # ── Smoke test ─────────────────────────────────────────────────────────
        Step "Smoke test: GET http://$ALB_DNS$HEALTH_PATH"
        $MAX_RETRIES = 6
        for ($i = 1; $i -le $MAX_RETRIES; $i++) {
            try {
                $Response = Invoke-WebRequest -Uri "http://$ALB_DNS$HEALTH_PATH" -UseBasicParsing -TimeoutSec 10
                if ($Response.StatusCode -eq 200) {
                    Ok "Health check passed (HTTP 200)"
                    Write-Host "    Body: $($Response.Content)"
                    break
                }
            } catch {
                Write-Host "    Attempt $i/$MAX_RETRIES failed: $($_.Exception.Message)"
            }
            if ($i -eq $MAX_RETRIES) { Fail "Smoke test failed after $MAX_RETRIES attempts" }
            Start-Sleep -Seconds 10
        }

        Write-Host "`n✓ Deployment complete!" -ForegroundColor Green
        Write-Host "  ALB DNS : http://$ALB_DNS" -ForegroundColor White
        Write-Host "  ECR     : $ECR_URL" -ForegroundColor White
    }
} else {
    Ok "Terraform skipped"
}
