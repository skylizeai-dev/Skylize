#Requires -Version 5.1
#
# ASCII ONLY. DO NOT REINTRODUCE NON-ASCII CHARACTERS TO THIS FILE.
#
# This script could not be run by anyone: [Parser]::ParseFile reported 21 parse
# errors under PowerShell 5.1 and the file never loaded. One cause was a POSIX
# `||` inside an interpolated $(...) (fixed further down). The other was
# encoding, and it is the reason for this banner.
#
# The file is UTF-8 with no BOM. PowerShell 5.1 decodes a BOM-less .ps1 using
# the system ANSI codepage, not UTF-8. This file contained 501 box-drawing
# characters, one em-dash, three arrows and a check mark. Decoded that way an
# em-dash (U+2014, UTF-8 E2 80 94) becomes three characters ending in U+201D --
# a RIGHT DOUBLE QUOTATION MARK, which PowerShell honours as a string
# delimiter. Verified: parsing `"abc` + U+201D yields ZERO errors, i.e. the
# smart quote closed the string. So every em-dash injected a stray string
# terminator, which is why `Ok "Terraform skipped"` on the last line was
# reported unterminated and two `if` blocks were reported unclosed.
#
# This is not local to one machine: 0x94 maps to U+201D in windows-1252 as
# well as the windows-1254 measured here, so any western Windows ANSI codepage
# breaks the same way.
#
# CLAUDE.md already required plain ASCII in this repo. All 506 non-ASCII
# characters were replaced with ASCII equivalents; the file now parses with 0
# errors. A UTF-8 BOM would also have worked, but ASCII is the stated
# convention and does not depend on every consumer honouring the BOM.
#
<#
.SYNOPSIS
    Deploy Skylize to AWS staging.
.DESCRIPTION
    Runs: Terraform init/plan/apply -> ECR push -> DB migrations -> smoke test.
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

# -- Config --------------------------------------------------------------------
$AWS_REGION      = "us-east-1"
$TF_DIR          = "infra\terraform\staging"
$ECR_REPO_NAME   = "skylize-api"
$ECS_CLUSTER     = "skylize-staging"
$ECS_SERVICE     = "skylize-staging-api"
$ECS_TASK_FAMILY = "skylize-staging-api"
$CONTAINER_NAME  = "api"
$HEALTH_PATH     = "/health"

# -- Helpers -------------------------------------------------------------------
function Step { param([string]$Msg) Write-Host "`n==> $Msg" -ForegroundColor Cyan }
function Ok   { param([string]$Msg) Write-Host "    OK: $Msg" -ForegroundColor Green }
function Fail { param([string]$Msg) Write-Host "FAIL: $Msg" -ForegroundColor Red; exit 1 }

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "$Name not found in PATH. Install it and retry."
    }
}

# -- Pre-flight checks ---------------------------------------------------------
Step "Pre-flight checks"
Assert-Command aws
Assert-Command terraform
Assert-Command docker

$Identity = aws sts get-caller-identity --output json 2>&1
if ($LASTEXITCODE -ne 0) { Fail "AWS CLI not authenticated. Run: aws sso login OR set AWS_PROFILE" }
$AccountId = ($Identity | ConvertFrom-Json).Account
Ok "AWS account: $AccountId"

# -- Bootstrap Terraform state backend (idempotent) ----------------------------
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

# -- Terraform -----------------------------------------------------------------
if (-not $SkipTerraform) {
    Push-Location $TF_DIR

    Step "Terraform init"
    terraform init -upgrade
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "terraform init failed" }

    Step "Terraform plan"
    terraform plan -out=tfplan
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "terraform plan failed" }

    if ($PlanOnly) {
        Ok "Plan only - skipping apply."
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

        # -- Docker build + ECR push --------------------------------------------
        Step "ECR login"
        aws ecr get-login-password --region $AWS_REGION |
            docker login --username AWS --password-stdin "$AccountId.dkr.ecr.$AWS_REGION.amazonaws.com"
        if ($LASTEXITCODE -ne 0) { Fail "ECR login failed" }

        # SHA-ONLY TAGS (owner decision, 2026-07-31), matching
        # .github/workflows/deploy-staging.yml. This script used to tag and
        # push :latest alongside the SHA. The push succeeds long before the
        # smoke test at the bottom of this file runs, so a container that
        # cannot boot was published under the tag everything else treats as
        # "the current staging image" -- including
        # infra/terraform/staging/main.tf's ecr_image_uri.
        # RESTORE :latest ONLY AFTER a deploy has completed green end to end
        # (service stable AND the /health smoke test returning 200). Until
        # then :latest would mean "newest image, boot status unknown".
        $IMAGE_TAG = (git rev-parse --short HEAD 2>$null)
        if (-not $IMAGE_TAG) { $IMAGE_TAG = "initial" }
        $FULL_TAG  = "${ECR_URL}:${IMAGE_TAG}"

        # -f Dockerfile is the ONE gateway Dockerfile. infra/Dockerfile was
        # deleted on 2026-07-31; do not reintroduce a second definition.
        Step "Docker build"
        docker build -t $FULL_TAG -f Dockerfile .
        if ($LASTEXITCODE -ne 0) { Fail "Docker build failed" }

        Step "Docker push to ECR"
        docker push $FULL_TAG
        if ($LASTEXITCODE -ne 0) { Fail "Docker push failed" }
        Ok "Image pushed: $FULL_TAG"

        # -- Populate Secrets Manager (prompt if not set) -----------------------
        Step "Secrets Manager - populate (this script does NOT populate them)"
        # SINGLE-QUOTED here-string (@'...'@), deliberately.
        #
        # This block was @"..."@ -- double-quoted -- and it did not parse. A
        # double-quoted here-string interpolates $(...) at PARSE time, and the
        # GOVERNANCE_SIGNING_KEY_PEM line held
        #   $(python scripts/gen_governance_key.py 2>/dev/null || echo '...')
        # which is POSIX shell, not PowerShell. `||` is not a PowerShell 5.1
        # operator, so the whole file failed to load with 21 parse errors and
        # deploy.ps1 could never have been run by anyone. Verified with
        # [Parser]::ParseFile.
        #
        # The interpolation was also a secret-handling defect in its own right:
        # had it parsed, it would have EXECUTED the key generator and printed a
        # freshly minted ECDSA P-384 PRIVATE KEY to the console, and into the
        # transcript of whatever ran it. Private keys do not belong in log
        # output. The instruction below names the generator instead of running
        # it.
        #
        # The examples are single-line so they are correct in both PowerShell
        # and bash; the previous `\` continuations were bash-only and this is a
        # PowerShell script.
        Write-Host @'
  REMINDER: these eight secret shells are created EMPTY by terraform.
  Populate every one of them before the service can boot. This script does not
  populate them and must not: the values are yours, not the tooling's.

    aws secretsmanager put-secret-value --secret-id /skylize/staging/DATABASE_URL --secret-string "postgresql://skylize:<password>@<rds-endpoint>:5432/skylize"

    aws secretsmanager put-secret-value --secret-id /skylize/staging/DATABASE_APP_URL --secret-string "postgresql://skylize_app:<app-password>@<rds-endpoint>:5432/skylize"

    aws secretsmanager put-secret-value --secret-id /skylize/staging/REDIS_URL --secret-string "redis://<elasticache-endpoint>:6379"

    aws secretsmanager put-secret-value --secret-id /skylize/staging/HMAC_SECRET --secret-string "<random-32-bytes>"

    aws secretsmanager put-secret-value --secret-id /skylize/staging/JWT_SECRET --secret-string "<random-32-bytes>"

    aws secretsmanager put-secret-value --secret-id /skylize/staging/DB_PASSWORD --secret-string "<rds-master-password>"

    Governance signing key - generate it, then paste the PEM. Do not pipe a
    private key through this console:
      python scripts/gen_governance_key.py    (writes the PEM; keep it out of the repo)
      aws secretsmanager put-secret-value --secret-id /skylize/staging/GOVERNANCE_SIGNING_KEY_PEM --secret-string file://<path-to-pem>

  TWO THINGS THAT WILL STILL STOP THE CONTAINER BOOTING:

    1. DATABASE_APP_URL must name the skylize_app role, NOT the skylize master
       user. Settings refuses an app DSN equal to the admin DSN, and past that
       bootstrap.py reads pg_roles on the live pool and refuses to start if the
       runtime role is SUPERUSER or BYPASSRLS.

    2. skylize_app is created by migration 0003, which runs from the image CMD
       and reads SKYLIZE_APP_DB_PASSWORD from the environment. The ECS task
       definition does not supply it, so the role is created LOGIN with NO
       password and the password in DATABASE_APP_URL cannot authenticate. Wire
       SKYLIZE_APP_DB_PASSWORD into the task definition and make it match the
       password inside DATABASE_APP_URL before expecting a green /health.

'@ -ForegroundColor Yellow

        # -- Run DB migrations via ECS one-off task -----------------------------
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

        # -- Force ECS service update -------------------------------------------
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

        # -- Smoke test ---------------------------------------------------------
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

        Write-Host "`n[OK] Deployment complete!" -ForegroundColor Green
        Write-Host "  ALB DNS : http://$ALB_DNS" -ForegroundColor White
        Write-Host "  ECR     : $ECR_URL" -ForegroundColor White
    }
} else {
    Ok "Terraform skipped"
}
