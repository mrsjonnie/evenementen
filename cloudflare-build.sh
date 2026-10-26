#!/usr/bin/env bash
set -euo pipefail

# Publish only the website files needed at runtime. Do not expose scraper
# source code, workflow files, logs, configuration, or feedback JSON.
rm -rf public
mkdir -p public

cp index.html evenementen.html restaurants.html instellingen.html events.json sites.json scrape-progress.json public/
cp _headers public/_headers

echo "Cloudflare Pages output prepared:"
find public -maxdepth 1 -type f -printf '%f\n' | sort
