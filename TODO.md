## TODO

### Completed
- [X] Add pagination to the static information
- [X] Update the database
- [X] Implement search with fuzzy matching
- [X] Add filters (open, water activities, boating, forest)
- [X] Format weather display (remove raw JSON)
- [X] Improve UI/UX for home and results pages
- [X] Add error handling throughout app

### High Priority
- [ ] Test app with real database
- [ ] Optimize dynamic update script for API quota limits
- [ ] Add pagination to results page
- [ ] Add more amenity filters (restrooms, water availability)
- [ ] Implement date range search with weather forecasts

### Medium Priority
- [ ] Clean up static information in database
- [ ] Scrape remaining static information
- [ ] Add image support to campsite pages
- [ ] Implement caching for frequently searched sites
- [ ] Add loading states/spinners

### Low Priority
- [ ] Map integration with Leaflet
- [ ] Fire restriction alerts
- [ ] Reservation availability checking
- [ ] User favorites system
- [ ] Mobile UI improvements

### Technical Debt
- [ ] Move filters to SQL query instead of Python filtering
- [ ] Add database indexes for common queries
- [ ] Set up proper logging instead of print statements
- [ ] Add unit tests for search and filter logic
- [ ] Database backup strategy