**This project is a Flask-based web application that helps users plan and explore commute routes using external transport data. This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.**

It integrates with:

Transport for London (TfL API) for live/public transport data

OpenRouteService (ORS API) for route calculations and mapping

-------------------------
-------------------

Backend:

    Python
    Flask (web framework)
    Frontend

HTML (templates):

    CSS (styling)
    JavaScript (client-side logic)

APIs:

    TfL API → transport data (stations, arrivals, etc.)
    ORS API → route planning, directions, mapping

Make sure you have:

    Python 3.8+
    Git

----------------------------
----------------------------

**How the App Works:**

1. User loads a page (Flask serves HTML from /templates)

2. Frontend JavaScript captures user input (locations, routes)

3. Flask backend:

    a. Calls TfL API → retrieves transport data

    b. Calls ORS API → calculates routes

4. Data is returned and rendered in the UI

-----------------------
------------------------

**How to Set up:**

1. Clone the repository `git clone https://github.com/emurph91/commute-app.git`

2. Create and activate a virtual environment in folder directory (optional but highly recommended). `py -m venv`

3. Install dependencies using requirements.txt. `pip install -r requirements.txt`

4. API key setup

    a. TFL API: Sign up via the Transport for London portal (https://api.tfl.gov.uk/). Generate an App ID and App Key

    b. ORS API (https://api.openrouteservice.org/): Register with OpenRouteService. Get your API key

5. Save API keys in your directory folder. Update the code in app.py to reflect your exact naming of the API key files.

6. Download transport CSV from .gov website:  https://beta-naptan.dft.gov.uk/download/la
    
    a. Choose Greater London/London (490) as your local authority search.

    b. Choose CSV file type as download.

    c. Ensure it is within your commute_app folder directory

    d. This data set needs to be cleaned, a "mode" column is required with bus, rail, tube, and ferry categories aggregating the data. 
    
    e. See mode_table.csv for help. 

7. Run the Application `py app.py`

8. In the terminal a http link (http://127.0.0.1:5000) will appear, click to open the web application.

9. Input commute criteria and click search. NOTE: Searches can take up to 5mins due to numerous API calls. 

10. To stop the app: press Ctrl + c, inside the terminal.
--------------------------------
--------------------------------
**Project Structure**

commute-app/

│ app.py              

│ templates/               # HTML templates

│ static/

    │   ├ css/             # Stylesheets
    │   └ js/              # JavaScript

│ requirements.txt         # Dependencies

│ .env                     # API keys (not committed)

| 490Stops.csv

----------------------------
-----------------------