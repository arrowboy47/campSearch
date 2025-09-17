import argparse
import requests
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import psycopg2
import pandas as pd
import os


# Set up argument parser
parser = argparse.ArgumentParser(description="Update dynamic data for a single campsite.")
parser.add_argument("campsite_id", type=int, help="The ID of the campsite to update")

args = parser.parse_args()
campsite_id = args.campsite_id

print(f"Updating campsite ID: {campsite_id}")
# link to the database
conn = psycopg2.connect(dbname="camping", user="arrowboy")
cur = conn.cursor()

# Fetch the site_url for the given campsite_id
cur.execute("SELECT site_url FROM campsites WHERE id = %s;", (campsite_id,))
result = cur.fetchone()

# soup parser
r = requests.get(result[0])
soup = BeautifulSoup(r.text, "html.parser")
print(soup.prettify())

# get the site_url