# DealRadar on Google Cloud

Project `dealradar-515523`, region `europe-west1` (Belgium). Deployed 2026-11-23.

The project has two parts:

1. **Data collection and storage**: the Cloud Run Job `dealradar-collect` runs the three collectors (operators, retail chains, Amazon.ie), merges them, decides automatically what to publish, and writes the result to Firestore and Cloud Storage.
2. **UI and API**: the Cloud Run service `dealradar-web` reads Firestore and serves the page and JSON API. The mobile123 site and the App can read the same API later.

## Resources

| Resource | Name | Purpose |
|---|---|---|
| Firestore (Native, europe-west1) | `(default)` | `deals` (one doc per record), `price_history`, `runs`, `meta/latest` |
| Cloud Storage | `gs://dealradar-515523-data` | `state/` files the next run needs (previous offers, price history, Amazon search cursor); `runs/` and `evidence/` per run, deleted after 90 days; `archive/local-2026-11-22/` = the data collected on the Mac before migration |
| Cloud Run Job | `dealradar-collect` | `python cloud/run_collect.py`, 1 CPU, 1 GiB, 45 min timeout |
| Cloud Run service | `dealradar-web` | https://dealradar-web-815864259513.europe-west1.run.app (public) |
| Cloud Scheduler | `dealradar-collect-07`, `dealradar-collect-19` | 07:00 and 19:00 Europe/Dublin |
| Artifact Registry | `europe-west1-docker.pkg.dev/dealradar-515523/dealradar/app` | one image for the job and the service |
| Build logs | `gs://dealradar-515523-buildlogs` | Cloud Build output |

Service accounts (least privilege): `dealradar-collector` (Firestore read/write, data bucket objects), `dealradar-web` (Firestore read only), `dealradar-scheduler` (may start the job), `dealradar-builder` (read build source, push images, write build logs).

## Web endpoints

- `/` public page: automatically published deals only
- `/internal?key=...` every record with its publish decision and reason (key in Secret Manager)
- `/api/deals` published deals as JSON (`?module=operator|retailer|amazon`, `?discount=1`)
- `/api/deals/<id>`, `/api/runs/latest`, `/health`


## Automatic publication (no human step)

`outputs/combine_deals.py` `auto_publish()`:

- Operators: published when the collector's term checks pass (`can_list`).
- Retail chains: published when the price was read directly from the retailer's site in this run and is still fresh. If this collector's own history saw a lower price in the previous 30 days, the Was price and discount are hidden and only the current price is shown. Snapshots without a known read time are never published.
- Amazon: published with the price from the Amazon.ie product page and the time it was read, and a `mobile123ie-21` link.
- Anything past `fresh_until`, unconfirmed or stale is not published.

## Amazon.ie collection

Owner decision (2026-11-23): collect from Amazon.ie directly instead of waiting for the Creators API. Order per run:

1. Third-party deal sites (`web_collect.py`) give candidate ASINs.
2. Each candidate is opened on its Amazon.ie product page (3 s apart); only discounts > 40 % shown on that page are kept, with seller, stock text and read time.
3. Up to 6 Amazon.ie keyword searches per run (10 s apart), rotating through `config.json` keywords via `state/amazon/search-cursor.json`; hits are confirmed on their product pages.

Plain requests only. Amazon's bot challenge appeared after ~10 quick searches in testing; any challenge stops that step and the rest of the run continues. Amazon's Conditions of Use and the Associates Operating Agreement restrict automated access and price display; the owner accepted that risk. If `AMAZON_CREATORS_CLIENT_ID` and `AMAZON_CREATORS_CLIENT_SECRET` are later set on the job (from Secret Manager), the job switches to the official API automatically.

## Deploying a change

Push to `main` on https://github.com/nexloveie-ops/DealRadar. The Cloud Build trigger `dealradar-main` (europe-west1, GitHub connection `dealradar-github`) runs `cloudbuild.yaml` as `dealradar-builder`:

1. builds the image; the Docker build runs every module's tests and stops on any failure;
2. pushes `app:<BUILD_ID>` and `app:latest`;
3. deploys `dealradar-web` and updates `dealradar-collect` to that image.

Build logs go to `gs://dealradar-515523-buildlogs`. Run one collection by hand with `gcloud run jobs execute dealradar-collect --region europe-west1 --project dealradar-515523`; rebuild Firestore from saved state without collecting with `--args=cloud/run_collect.py,--load-only`.

## Public access

`dealradar-web` allows unauthenticated access (`allUsers` has `roles/run.invoker`): https://dealradar-web-815864259513.europe-west1.run.app. `/internal` returns 404 unless `?key=` matches the `INTERNAL_KEY` secret (`dealradar-internal-key` in Secret Manager):

```sh
gcloud secrets versions access latest --secret dealradar-internal-key --project dealradar-515523
```

## Known limitation: Amazon from Google Cloud

From Cloud Run, Amazon.ie hides product-page prices ("add this item to your basket") and answers searches with 503. The same requests from a home connection returned prices. Not worked around. Options: run the Amazon step on a home machine and upload its output, or use the Creators API.

## Cost

At this volume: Firestore and Cloud Scheduler stay within the free tier; Cloud Run job ~25 min/day; storage a few hundred MB. Expect roughly €1–3 per month.
