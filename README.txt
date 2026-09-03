# U.S. Cyclist Crash Monitor — v10.0

Fresh standalone rebuild.

## What it monitors
- Cyclists/bicyclists killed in motor-vehicle crashes
- Cyclists/bicyclists with serious or critical injuries
- U.S. and international reports
- Duplicate reports consolidated
- State/location filtering
- 1 day, 7 day, 30 day, 6 month, and 1 year periods

## News discovery
The scanner independently queries:
- Google News RSS
- Bing News RSS

It uses broad injury language, including:
critical condition, critically hurt, spinal injury, spinal cord injury,
hospitalized, trauma center, possible paralysis, multiple injuries,
serious trauma, and related terms.

## Important regression case
A verified Miami/Rickenbacker Causeway cyclist injury from August 29, 2026
is included in a fresh database as a regression test. The live scanner also
searches independently for the report, so the seed is not the scanner itself.

## Run on Windows
Open a terminal in this folder and run:

python app.py

Then open:

http://127.0.0.1:5000/?period=30d&location=Florida

Click "Scan for new reports".

## Requirements
Python 3.9+ and internet access for live scanning.

## Render deployment
Runtime: Python 3
Build command: pip install -r requirements.txt
Start command: gunicorn --bind 0.0.0.0:$PORT app:app
