# procurement-environment-pipeline
Data engineering pipeline integrating EU public procurement, air quality, and agricultural data to analyze environmental spending and sustainability trends across European regions.

## Data sources

- **TED** — public procurement data
- **EEA** — air quality data
- **Eurostat** — regional and agricultural data

## Pipeline

Each source has its own `ingestion` module (raw data download) and `normalization` module (converting raw data into a common format). Shared logic lives in `common`.
