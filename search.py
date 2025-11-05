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
    the search also fuzzy match the forest name as well as the campsite name: if the forest name is the best match, it will return all campsites from that forest and sort them by their name match score
    '''
    
    '''
    Ways to improve this:
    1. Have preliminary filtering of other features like activities, amenities, etc. Then, fuzzy match the name on those results
    2. make a counter of the number a times a campsite has been selected from a search result and use that to weight the score
    '''
    conn = get_connection()
    cur = conn.cursor()
    
    # get all campsites from the database
    # we will see how inefficient this is
    cur.execute("SELECT id, name, forest_name FROM campsites;")
    all_sites = cur.fetchall()
    
    query = normalize(query)
    name_results = []
    forest_scores = {}
    # loop through site names and find the closest match
    for site_id, name, forest_name in all_sites: # the comma helps unpack the tuple 
        name_flat = normalize(name)
        forest_flat = normalize(forest_name)
        
        # name scores
        token_score = fuzz.token_sort_ratio(query, name_flat) # best for jumbled words: words in wrong order
        partial_score = fuzz.partial_ratio(query, name_flat) # best for having missing letters/words in query that are in the name
        exact_substring = int(query in name_flat) * 100
        # Simply returning the max of the three
        site_score = max(token_score, partial_score, exact_substring)
        
        # forest name scores
        partial_forest_score = fuzz.partial_ratio(query, forest_flat) # best for having missing letters/words in query that are in the forest name
        exact_forest_substring = int(query in forest_flat) * 100
        forest_score = max(partial_forest_score, exact_forest_substring)

        # Store top site matches
        if site_score == 100 and forest_score != 100:
            return [{"id": site_id, "name": name, "score": site_score}]

        if site_score >= fuzzthresh:
            name_results.append({
                "id": site_id,
                "name": name,
                "forest_name": forest_name,
                "score": site_score
            })

        # Collect forest match scores for group-based fallback
        if forest_score >= fuzzthresh:
            forest_scores.setdefault(forest_name, []).append((site_id, name, site_score))

    # Check if any forest name matched better than site names
    # if exact_forest_substring | partial_forest_score > score:
    best_forest_match = max(forest_scores.items(), key=lambda item: fuzz.partial_ratio(query, normalize(item[0])), default=None)

    if best_forest_match:
        forest_name, site_list = best_forest_match
        # Return all campsites from this forest, sorted by their name match scores
        sorted_sites = sorted(site_list, key=lambda x: x[2], reverse=True)
        return [{"id": site_id, "name": name, "forest_name": forest_name, "score": score} for site_id, name, score in sorted_sites]

    # if forest is not the best match, return top name matches
    name_results.sort(key=lambda x: x["score"], reverse=True)
    return name_results[:limit]
