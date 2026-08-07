#!/usr/bin/env bash
# Bootstrap GitHub Actions OIDC + Terraform state for Turbo Notes (account 615737882760)
set -euo pipefail

ACCOUNT_ID="${AWS_ACCOUNT_ID:-615737882760}"
REGION="${AWS_REGION:-us-east-2}"
PROFILE="${AWS_PROFILE:-turboai-615737882760}"
ROLE_NAME="${GITHUB_OIDC_ROLE_NAME:-turboai-notes-github-actions}"
STATE_BUCKET="turboai-notes-tfstate-${ACCOUNT_ID}"
LOCK_TABLE="turboai-notes-tflock"

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"

echo "Using profile=$AWS_PROFILE region=$REGION"
aws sts get-caller-identity

# OIDC provider for GitHub Actions
# Note: create-open-id-connect-provider returns OpenIDConnectProviderArn (not Arn).
OIDC_ARN=$(aws iam list-open-id-connect-providers --output json \
  | python3 -c "import sys,json; ps=json.load(sys.stdin).get('OpenIDConnectProviderList') or []; print(next((p['Arn'] for p in ps if 'token.actions.githubusercontent.com' in p.get('Arn','')), ''))")
if [[ -z "$OIDC_ARN" ]]; then
  echo "Creating GitHub OIDC provider..."
  OIDC_ARN=$(aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
    --query OpenIDConnectProviderArn --output text)
fi
if [[ -z "$OIDC_ARN" || "$OIDC_ARN" == "None" ]]; then
  echo "ERROR: could not resolve GitHub OIDC provider ARN" >&2
  exit 1
fi
echo "OIDC_ARN=$OIDC_ARN"

# Trust policy: only jhgcc1 turboai-notes-* repos
TRUST=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "${OIDC_ARN}"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": [
          "repo:jhgcc1/turboai-notes-api:*",
          "repo:jhgcc1/turboai-notes-web:*"
        ]
      }
    }
  }]
}
EOF
)

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "Role exists, updating trust..."
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST"
else
  echo "Creating role $ROLE_NAME..."
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" \
    --description "GitHub Actions deploy for Turbo Notes"
fi

# Broad deploy policy (demo account) — tighten later
POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect":"Allow","Action":["ecr:*","ecs:*","apprunner:*","rds:*","ec2:*","s3:*","cloudfront:*","logs:*","cloudwatch:*","iam:PassRole","iam:GetRole","iam:CreateRole","iam:AttachRolePolicy","iam:PutRolePolicy","iam:TagRole","iam:CreateInstanceProfile","iam:AddRoleToInstanceProfile","iam:GetInstanceProfile","ssm:GetParameter","dynamodb:*","elasticloadbalancing:*"],"Resource":"*"}
  ]
}
EOF
)
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name TurboNotesDeploy --policy-document "$POLICY"

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)
echo "ROLE_ARN=$ROLE_ARN"

# Terraform state backend
if ! aws s3api head-bucket --bucket "$STATE_BUCKET" 2>/dev/null; then
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
  aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "$STATE_BUCKET" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
fi

if ! aws dynamodb describe-table --table-name "$LOCK_TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --table-name "$LOCK_TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST --region "$REGION"
  aws dynamodb wait table-exists --table-name "$LOCK_TABLE"
fi

echo "$ROLE_ARN" > /tmp/turbo-notes-role-arn.txt
echo "Done. Set GitHub secret AWS_ROLE_ARN=$ROLE_ARN"
