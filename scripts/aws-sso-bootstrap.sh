#!/usr/bin/env bash
# Bootstrap AWS SSO profile for Turbo Notes account 615737882760.
set -euo pipefail

ACCOUNT_ID="${AWS_ACCOUNT_ID:-615737882760}"
PROFILE="${AWS_PROFILE_NAME:-AdministratorAccess-${ACCOUNT_ID}}"
REGION="${AWS_REGION:-us-east-1}"
SSO_START_URL="${AWS_SSO_START_URL:-}"
SSO_REGION="${AWS_SSO_REGION:-us-east-1}"

CONFIG="${HOME}/.aws/config"
mkdir -p "${HOME}/.aws"

if grep -q "\[profile ${PROFILE}\]" "${CONFIG}" 2>/dev/null; then
  echo "Profile ${PROFILE} already exists."
else
  if [[ -z "${SSO_START_URL}" ]]; then
    echo "Set AWS_SSO_START_URL to your IAM Identity Center start URL, e.g.:"
    echo "  export AWS_SSO_START_URL=https://your-org.awsapps.com/start"
    echo "Then re-run this script."
    exit 1
  fi
  cat >> "${CONFIG}" <<EOF

[profile ${PROFILE}]
sso_start_url = ${SSO_START_URL}
sso_region = ${SSO_REGION}
sso_account_id = ${ACCOUNT_ID}
sso_role_name = AdministratorAccess
region = ${REGION}
output = json
EOF
  echo "Wrote profile ${PROFILE}"
fi

echo "Logging in via SSO (browser may open)..."
aws sso login --profile "${PROFILE}"
aws sts get-caller-identity --profile "${PROFILE}"
echo "SSO ready for account ${ACCOUNT_ID}"
