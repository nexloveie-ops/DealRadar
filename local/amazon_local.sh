#!/bin/zsh
# DealRadar Amazon step on the owner's Mac (Amazon.ie hides prices from Google Cloud addresses).
# 1. download the previous Amazon state from Cloud Storage
# 2. third-party leads (web_collect.py), then Amazon.ie re-check + leads + rotating searches (amazon_direct.py)
# 3. upload to gs://dealradar-515523-data/state/amazon*/ unless the run produced nothing
# 4. ask the cloud job to reload Firestore (--load-only), so the site updates within minutes
# Scheduled by ~/Library/LaunchAgents/ie.mobile123.dealradar-amazon.plist (06:30 and 18:30 Irish time).
set -u
ROOT="${0:A:h:h}"
PY="$ROOT/.venv/bin/python"
GCLOUD="${GCLOUD:-/Users/admin/.homebrew/bin/gcloud}"
PROJECT=dealradar-515523
REGION=europe-west1
BUCKET="gs://$PROJECT-data"
DATA="$HOME/Library/Application Support/DealRadar/amazon"
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$DATA/leads" "$DATA/direct" "$DATA/cache-leads" "$DATA/cache-direct" "$DATA/logs"
exec >>"$DATA/logs/$TS.log" 2>&1
echo "== DealRadar Amazon local run $TS"

"$GCLOUD" storage cp -q "$BUCKET/state/amazon_leads/offers.json" "$DATA/leads/offers.json" || echo "no previous leads"
"$GCLOUD" storage cp -q "$BUCKET/state/amazon/offers.json" "$DATA/previous.json" || echo "no previous Amazon offers"
"$GCLOUD" storage cp -q "$BUCKET/state/amazon/search-cursor.json" "$DATA/direct/search-cursor.json" || echo "no search cursor"

cd "$ROOT/outputs/amazon-offers-service" || exit 1
"$PY" web_collect.py --cache "$DATA/cache-leads" --out "$DATA/leads"
echo "web_collect exit $?"
"$PY" amazon_direct.py --cache "$DATA/cache-direct" --out "$DATA/direct" --leads "$DATA/leads/offers.json" --previous "$DATA/previous.json"
echo "amazon_direct exit $?"

COUNT=$("$PY" -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["offers"]))' "$DATA/direct/offers.json" 2>/dev/null || echo 0)
echo "Amazon offers to publish: $COUNT"
if [[ "$COUNT" -eq 0 ]]; then
  echo "Nothing confirmed and nothing carried over; keeping the previous cloud state."
  exit 2
fi
"$GCLOUD" storage cp -q "$DATA/leads/offers.json" "$BUCKET/state/amazon_leads/offers.json"
for f in offers.json search-cursor.json run-report.json rejected.json; do
  [[ -f "$DATA/direct/$f" ]] && "$GCLOUD" storage cp -q "$DATA/direct/$f" "$BUCKET/state/amazon/$f"
done
"$GCLOUD" storage cp -q "$DATA/direct/run-report.json" "$BUCKET/runs/local-$TS/amazon/run-report.json"
"$GCLOUD" run jobs execute dealradar-collect --project "$PROJECT" --region "$REGION" --async \
  --args=cloud/run_collect.py,--load-only
echo "uploaded; Firestore reload requested"
find "$DATA/logs" -name '*.log' -mtime +30 -delete
