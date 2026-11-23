# DealRadar

Mobile123 Ireland deals: collects phone and consumer-electronics offers from Irish mobile operators, retail chains and Amazon.ie, decides automatically what to publish, stores the result in Firestore and serves it as a web page and JSON API.

| Folder | What it is |
|---|---|
| `outputs/prepay-offers-service/` | Vodafone, Three, eir, Tesco Mobile prepay handset offers |
| `outputs/retailer-offers-service/` | Powercity and Expert Ireland catalogue prices, Was prices and own price history |
| `outputs/amazon-offers-service/` | Amazon.ie: third-party leads, keyword search and product-page confirmation, Associates links |
| `outputs/combine_deals.py` | Merges the three modules and applies the automatic publication rules |
| `cloud/` | Cloud Run Job (`run_collect.py`), web service (`web.py`) and deployment notes |

Every push to `main` is built and deployed by Cloud Build on Google Cloud project `dealradar-515523` (see `cloudbuild.yaml`). The image build runs every module's tests and stops on any failure. Deployment details: [cloud/README.md](cloud/README.md).
