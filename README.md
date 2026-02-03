# Camp Search

A web application for searching and filtering California campsites with real-time weather and status information.

## Overview

Camp Search solves the problem of finding open campsites in California by aggregating data from USDA Forest Service sites and providing powerful search and filtering capabilities. Unlike the official government sites, Camp Search offers:

- Fuzzy search (find campsites even with typos)
- Real-time weather forecasts
- Open/closed status filtering
- Amenity filtering (water activities, boating, etc.)
- Clean, modern UI

## Features

### Current Features
- **Fuzzy Search**: Find campsites and forests even with partial names
- **Smart Filtering**:
  - Open/closed status
  - Water activities
  - Boating availability
  - Forest selection
- **Weather Integration**: Displays temperature and precipitation forecasts
- **Modern UI**: Clean, responsive design with filter-on-focus behavior

### Database
- PostgreSQL with 1000+ California campsites
- Static data: location, amenities, fees, seasons
- Dynamic data: weather forecasts, open/closed status

## Quick Start

### Prerequisites
- Python 3.x
- PostgreSQL
- OpenWeather API key

### Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install flask psycopg2 requests beautifulsoup4 pandas rapidfuzz
   ```

3. Set up PostgreSQL database:
   ```bash
   psql -U your_user -d camping -f schema.sql
   ```

4. Run the app:
   ```bash
   python app.py
   ```

5. Visit `http://localhost:5000`

## Project Structure

```
├── app.py                 # Flask application and routes
├── db.py                  # Database connection helpers
├── search.py              # Fuzzy search logic
├── weather.py             # OpenWeather API integration
├── templates/             # HTML templates
│   ├── index.html        # Home/search page
│   ├── results.html      # Search results with filters
│   └── campsite.html     # Individual campsite details
├── scripts/              # Data scraping and updates
│   ├── pull_static_info_for_park.py
│   └── dynamic.py        # Weather/status updates
└── schema.sql            # Database schema
```

## Usage

1. **Search**: Enter a campsite or forest name
2. **Filter**: Click search box to reveal filters
3. **Browse**: View results with weather and status
4. **Details**: Click campsite name for full information

## Recent Updates

See [IMPROVEMENTS.md](IMPROVEMENTS.md) for detailed changelog of recent improvements including:
- Enhanced filtering system
- Formatted weather display
- Error handling
- UI/UX improvements

## Technical Details

### Stack
- **Backend**: Flask (Python)
- **Database**: PostgreSQL
- **APIs**: OpenWeather
- **Scraping**: BeautifulSoup4
- **Search**: RapidFuzz

### Database Schema
- `campsites` - Core site information
- `amenities` - Site amenities and features
- `status_updates` - Open/closed status
- `weather_forecasts` - Cached weather data
- `images` - Site photos (future)
- `availability` - Reservation data (future)

## Contributing

This is a personal project, but suggestions are welcome. See TODO.md for planned features.

## License

Personal project - no formal license

## Future Plans

- Map integration with Leaflet
- Reservation availability checking
- Fire restriction alerts
- Mobile app
- User accounts and favorites

---

For detailed technical documentation, see the inline comments in the code files.
