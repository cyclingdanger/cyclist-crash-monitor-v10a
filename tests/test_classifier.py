import sys
sys.path.insert(0, ".")
from app import is_relevant_cyclist_report, status_from_text, location_from_text

TITLE = "Orthopedic surgeon suffers spinal injury after driver struck him while cycling on Rickenbacker Causeway in Miami"
DESC = "Dr. Gilbert Beauperthuy-Rojas remained hospitalized after a driver struck him while he was cycling in Miami's Virginia Key. He has a spinal cord injury and serious injuries."

assert is_relevant_cyclist_report(TITLE, DESC)
assert status_from_text(TITLE + " " + DESC) == "Seriously Injured"
assert location_from_text(TITLE, DESC)[0] == "Florida"

print("PASS: Miami regression case is cyclist + serious injury + Florida")
