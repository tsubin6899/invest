# Cloud market-data update

This folder can run without the local computer after it is pushed to GitHub.

## GitHub Actions

The workflow at `.github/workflows/update-market-data.yml` updates:

- `latest-prices.json`
- `latest-rates.json`

It runs on weekdays at 13:30 Taipei time and can also be triggered manually or by the web button.

## Web button trigger

Deploy this folder to Netlify if you want the in-page button to trigger GitHub Actions safely.

Set these Netlify environment variables:

- `GITHUB_TOKEN`: a fine-grained GitHub token with Actions write access for this repository
- `GITHUB_OWNER`: repository owner, for example `tsubin6899`
- `GITHUB_REPO`: repository name
- `GITHUB_REF`: branch name, usually `main`
- `GITHUB_WORKFLOW`: optional, defaults to `update-market-data.yml`
- `ALLOWED_ORIGIN`: optional, your site URL

The dashboard calls `/.netlify/functions/trigger-price-update`. The token stays in Netlify and is never exposed in the browser.
