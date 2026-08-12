# Google Maps List Exporter

Export a shared Google Maps saved list to text, with optional JSON and CSV files. The local web app streams extraction progress, place details, ETA, and a travelling map view as each place is processed.

## Local setup

Python 3.9 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python maps_export_ui.py
```

Open `http://127.0.0.1:8765`. The app stores the signed-in Chromium profile and generated exports in the parent directory by default, keeping private/runtime data outside the source tree.

You can override runtime paths and server settings:

```bash
MAPS_EXPORT_DATA_DIR=/path/to/data \
MAPS_PROFILE_DIR=/path/to/profile \
HOST=0.0.0.0 PORT=8765 \
python maps_export_ui.py
```

The command-line exporter is also available directly:

```bash
python extract_maps_lists.py "GOOGLE_MAPS_LIST_URL" --json --csv
```

## GitHub Pages

GitHub Pages can host the frontend, but it cannot run Python, Playwright, Chromium, or generate files. A Pages deployment therefore needs the Python service running on another host.

1. Host this repository's Python service on a platform that supports long-running Python processes and Playwright Chromium.
2. Set the backend's allowed frontend origin:

   ```bash
   ALLOWED_ORIGINS=https://YOUR-NAME.github.io
   HOST=0.0.0.0
   python maps_export_ui.py
   ```

3. In the GitHub repository, open **Settings > Secrets and variables > Actions > Variables** and create a repository variable named `MAPS_EXPORT_API_URL`. Set it to the public HTTPS URL of the Python service, without a trailing slash.
4. Push the repository to GitHub with `main` as the default branch.
5. In repository settings, choose **Pages > Build and deployment > Source > GitHub Actions**.

The workflow in `.github/workflows/pages.yml` injects that service URL at deploy time and publishes only `maps_export_ui/`. The header shows **Service connected** when the page can reach it. Keep `maps_profile/` private; it contains your Google browser session.

The final Pages URL will be `https://YOUR-NAME.github.io/REPOSITORY/`. GitHub Pages serves only the browser interface; the export continues to run on the separately hosted Python service.

## Data and security

- Never commit or upload `maps_profile/`.
- Treat exported lists as private data unless you intend to share them.
- Restrict `ALLOWED_ORIGINS` to the exact Pages origin or other trusted frontend origins.
- A remotely hosted Playwright service needs persistent storage if you want its Google session to survive redeployments.
