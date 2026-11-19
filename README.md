# ASIN Workbench

Streamlit application for evaluating US-sourced products for sale on Amazon Australia.

## Deploy to Streamlit Community Cloud

1. Create a GitHub repository, preferably private for your business app, or use an existing repository you administer.
2. Add the contents of this folder at the repository root. Preserve the `.streamlit/` folder. Lookup JSON files and the template are at the repository root. Do not upload a local `.venv` folder or source price/research reports.
3. Sign in at https://share.streamlit.io and select the workspace matching the repository owner.
4. Choose Create app, then the option to deploy your existing app from GitHub.
5. Select the repository, its branch, and `app.py` as the main file.
6. In Advanced settings choose Python 3.12, the tested Python version. No secrets are needed.
7. Deploy and wait for the application page to load. Keep access private for personal/business use unless you choose to share it.

The required files are `app.py`, `engine.py`, `requirements.txt`, `.streamlit/config.toml`, plus `referral_fees.json`, `duty_rates.json`, and `research_template.xlsx`. `test_engine.py` is included for verification.

## Use the app

Download the research template and fill only ASIN, Sub-category, Category. Upload that research list plus the US prices, AU prices, AU fee report, and package weight/sales-rank report. The original working workbook is supported; only MATCH ASIN (DEMAND & PRIORITY) is read.

Replacing reports recalculates by ASIN. US price uses the first direct match without offer filters. AU offers are filtered to buybox_winner=True before taking the first matching row. Price, seller, and fulfilment always come from that same AU row. Missing data generates explicit row errors. Old prices are never silently retained.

Referral and duty rules are bundled in JSON. Duty matches Product type/Sub-category and uses the first source-row rate. Fulfilment adjustments default to Amazon −1, FBA −1, FBM +3 AUD. Exchange rate defaults to 1.44 AUD/USD and logistics to AUD 10/kg. All may be adjusted in the sidebar.

Filter results, select final ASINs, and download Excel/CSV. Upload a previous export under Price changes to compare US price, AU price and P&L by ASIN.

Reports are transmitted to and processed by the Streamlit host when you upload them. The application does not save uploads to disk, contact Amazon APIs, or use a database. Streamlit keeps uploads, current selections and cached calculations in server memory. Browser sessions are separate, so opening the app from another device does not automatically restore your other device's uploads or selections. Keep exports if you need to resume or compare results later. Server restarts clear in-memory data.

The referral rules were extracted from your supplied workbook, with a stated reference date of 13 August 2026; they have not been independently verified against current Amazon rates.

## Verify or run locally

```sh
python -m pip install -r requirements.txt
python -m unittest -v test_engine
python -m streamlit run app.py
```

Official deployment instructions: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
