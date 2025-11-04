from rapidfuzz import fuzz
from db import get_connection
import re

def normalize(text):
    """
    Lowercases, strips, and collapses all whitespace to a single space.
    """
    return re.sub(r'\s+', '', text.strip().lower())

def get_campsite_by_name(query, fuzzthresh=50, limit=10):
    '''
    Return a list of campsites that match the campsite_name(query). Checks the database, uses rapidfuzz to findthe closest match
    Search algo has complexity of O(2n) (this prob is not correct but good enough) where n is the number of campsites in the database
    '''
    
    '''
    Ways to improve this:
    1. Have preliminary filtering of other features like activities, amenities, etc. Then, fuzzy match the name on those results
    2. make the search also fuzzy match the forest name as well as the campsite name
    '''
    conn = get_connection()
    cur = conn.cursor()
    
    # get all campsites from the database
    # we will see how inefficient this is
    cur.execute("SELECT id, name FROM campsites;")
    all_sites = cur.fetchall()
    
    query = normalize(query)
    results = []
    # loop through site names and find the closest match
    for site_id, name in all_sites: # the comma helps unpack the tuple 
        name_flat = normalize(name)
        token_score = fuzz.token_sort_ratio(query, name_flat) # best for jumbled words
        partial_score = fuzz.partial_ratio(query, name_flat) # best for having missing letters/words in query that are in the name
        exact_substring = int(query in name_flat) * 100

        # Simply returning the max of the three
        score = max(token_score, partial_score, exact_substring)

        if score == 100:
            results.append({"id": site_id, "name": name, "score": score})
            # break loop and return results
            break
        elif score >= fuzzthresh:
            results.append({"id": site_id, "name": name, "score": score})
            
    # Sort by best match
    results.sort(key=lambda x: x["score"], reverse=True)

    # return only the limit number of results
    return results[:limit]