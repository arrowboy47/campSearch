from rapidfuzz import fuzz
from db import get_connection
import re

def normalize(text):
    """
    Lowercases, strips, and collapses all whitespace to a single space.
    """
    return re.sub(r'\s+', '', text.strip().lower())

def get_campsite_by_name(query, fuzzthresh=40, limit=10):
    '''
    Return a list of campsites that match the campsite_name(query). Checks the database, uses rapidfuzz to findthe closest match
    Search algo has complexity of O(2n) (this prob is not correct but good enough) where n is the number of campsites in the database
    the search also fuzzy match the forest name as well as the campsite name: if the forest name is the best match, it will return all campsites from that forest and sort them by their name match score
    '''
    
    '''
    Ways to improve this:
    1. Have preliminary filtering of other features like activities, amenities, etc. Then, fuzzy match the name on those results
    2. make a counter of the number a times a campsite has been selected from a search result and use that to weight the score
    3. some sort of ML model to return best results
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
    best_site_score = 0
    best_forest_score = 0
    best_forest_match = None
    # loop through site names and find the closest match
    for site_id, name, forest_name in all_sites: # the comma helps unpack the tuple 
        name_flat = normalize(name)
        forest_flat = normalize(forest_name)
        
        # name scores
        token_score = fuzz.token_sort_ratio(name_flat, query) # best for jumbled words: words in wrong order
        partial_score = fuzz.partial_ratio(name_flat,query) # best for having missing letters/words in query that are in the name
        partial_adjusted_score = partial_score * (len(query) / len(name_flat)) # adjusting the score of the partial match to account for the length of the query being shorter than the name
        # Simply returning the max of the three
        site_score = max(token_score, partial_adjusted_score)
        best_site_score = max(best_site_score, site_score)
        
        # forest name scores
        partial_forest_score = fuzz.partial_ratio(query, forest_flat) * .6
        exact_forest_substring = int(query in forest_flat) * 100
        forest_score = max(partial_forest_score, exact_forest_substring)
        best_forest_score = max(best_forest_score, forest_score)

        # Store top site matches
        if site_score == 100 and forest_score != 100:
            return [{"id": site_id, "name": name, "score": site_score}]
     # Store site matches
        if site_score >= fuzzthresh:
            name_results.append({
                "id": site_id,
                "name": name,
                "forest_name": forest_name,
                "score": site_score
            })

        # Collect potential forest matches
        if forest_score >= fuzzthresh:
            forest_scores.setdefault(forest_name, []).append((site_id, name, site_score))

    # --- Decide if this is a forest search ---
    if best_forest_score >= fuzzthresh and best_forest_score > best_site_score:
        # Find the best forest match
        best_forest_match = max(
            forest_scores.items(),
            key=lambda item: fuzz.partial_ratio(query, normalize(item[0])),
            default=None
        )

        if best_forest_match:
            forest_name, site_list = best_forest_match
            sorted_sites = sorted(site_list, key=lambda x: x[2], reverse=True)
            return [
                {"id": sid, "name": sname, "forest_name": forest_name, "score": score}
                for sid, sname, score in sorted_sites
            ]

    # Otherwise, return top site matches
    name_results.sort(key=lambda x: x["score"], reverse=True)
    return name_results[:limit]

    # if forest is not the best match, return top name matches
    name_results.sort(key=lambda x: x["score"], reverse=True)
    return name_results[:limit]
