#!/bin/sh
set -eu

# This script is intentionally inert unless APPLY=true. A billing budget sends
# alerts; it is not a hard spend cap. The application-level Vertex guardrail must
# remain enabled as the enforcement boundary for judge traffic.
: "${PROJECT_ID:?set PROJECT_ID}"
: "${BILLING_ACCOUNT:?set BILLING_ACCOUNT (for example 011DA3-F8EB93-A5D676)}"
: "${BUDGET_AMOUNT_USD:?set BUDGET_AMOUNT_USD explicitly; do not guess a limit}"

BUDGET_NAME="${BUDGET_NAME:-Trip Agent Vertex Budget}"
FILTER_PROJECT="${FILTER_PROJECT:-projects/${PROJECT_ID}}"
APPLY="${APPLY:-false}"

case "${BUDGET_AMOUNT_USD}" in
  ''|*[!0-9.]*|.*|*.*.*)
    echo "BUDGET_AMOUNT_USD must be a positive USD amount such as 25 or 25.00." >&2
    exit 2
    ;;
esac

if [ "${BUDGET_AMOUNT_USD}" = "0" ] || [ "${BUDGET_AMOUNT_USD}" = "0.0" ] || [ "${BUDGET_AMOUNT_USD}" = "0.00" ]; then
  echo "BUDGET_AMOUNT_USD must be greater than zero." >&2
  exit 2
fi

GCLOUD="${GCLOUD_BIN:-gcloud}"
COMMON_ARGS="--billing-account=${BILLING_ACCOUNT} --filter-projects=${FILTER_PROJECT}"

if [ "${APPLY}" != "true" ]; then
  echo "Preflight only: no billing resource was changed."
  echo "Would create or reuse budget: ${BUDGET_NAME}"
  echo "Would scope usage to: ${FILTER_PROJECT}"
  echo "Would alert at: 50%, 80%, 100% current spend and 80% forecasted spend"
  echo "Would use amount: ${BUDGET_AMOUNT_USD} USD"
  exit 0
fi

"${GCLOUD}" services enable billingbudgets.googleapis.com --project="${PROJECT_ID}"

existing="$(${GCLOUD} billing budgets list ${COMMON_ARGS} \
  --filter="displayName=${BUDGET_NAME}" --format='value(name)' | sed -n '1p')"
if [ -n "${existing}" ]; then
  echo "Billing budget already exists: ${BUDGET_NAME}"
  exit 0
fi

"${GCLOUD}" billing budgets create ${COMMON_ARGS} \
  --display-name="${BUDGET_NAME}" \
  --budget-amount="${BUDGET_AMOUNT_USD}USD" \
  --calendar-period=month \
  --threshold-rule=percent=0.50 \
  --threshold-rule=percent=0.80 \
  --threshold-rule=percent=1.00 \
  --threshold-rule=percent=0.80,basis=forecasted-spend

echo "Billing budget created. Keep the application Vertex guardrail enabled; alerts do not hard-stop spend."
