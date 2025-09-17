"""
Heads up to future me: This is my first time making a flask app, so I'm going to overcomment the hell
out of this thing and try to treat comments as like learning tools so i can come back and know what tf going on
"""

from flask import Flask, request, jsonify, render_template
from db import get_campsite_by_id

# Create Flask app
app = Flask(__name__)

# routes tell the app what to do when a user goes to a certain url
# the index/home is the "root" of the site
@app.route("/")
def home():
    return render_template("index.html") # render_template is a function that opens a html file and renders it

@app.route("/api/campsite/<int:campsite_id>")
def get_campsite(campsite_id):
    data = get_campsite_by_id(campsite_id)
    if not data:
        return jsonify({"error": "Campsite not found"}), 404
    return jsonify(data)

# runs the app and runs the index route by default, I think?
# bash: p app.py
if __name__ == "__main__":
    app.run(debug=True)