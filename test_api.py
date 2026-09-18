import json
import requests

# Load the public sample cases provided by the organizers
with open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Grab the input for SAMPLE-01
test_input = data['cases'][0]['input']

print("Sending request to local API...")
response = requests.post('https://ju-meandrous.onrender.com/optimize-energy', json=test_input)

# Print the result
if response.status_code == 200:
    print("Success! Here is the API response:")
    print(json.dumps(response.json(), indent=2))
else:
    print(f"Error {response.status_code}: {response.text}")